# Instar Documentation

A safe, sandboxed disk image format converter.

## Overview

Instar replaces unsafe calls to `qemu-img` with a safer, sandboxed approach.
Image format conversions are performed within a KVM execution context,
providing strong isolation from the host system. You can read the
[announcement email I sent to the OpenStack mailing lists](/components/instar/openstack-announcement-email/)
if you're interested in my line of reasoning at the time.

The primary goal of `instar` is to be a safe drop in replacement for qemu-img.
The current focus is on `qemu-img info`, `qemu-img check`, `qemu-img compare`,
and `qemu-img convert` sub-commands, as the most painful parts in terms of
observed security exploits, but that will expand over time.
We therefore have a test suite of images that we run against both tools,
and any difference in output is considered a bug to be fixed -- if you
observe such a difference please report it as a GitHub issue at
https://github.com/shakenfist/instar. Obviously, providing an image which
demonstrates your concern, even if that image would otherwise be considered
malicious, is extremely helpful in fixing the bug and ensuring that we don't
regress later.

Along the pathway to complete equivalence, we have found a few examples of
`qemu-img` behaviour that we found counter intuitive. These are documented
on [our quirks page](/components/instar/quirks/), and you can suppress `qemu-img`
equivalence with the `--ignore-quirks` flag to `instar`.

Confused about how `instar` does these things? Perhaps read the
[technology primer](/components/instar/technology-primer/).

---

# Main Implementation Documentation

## Instar-Specific Features

Features unique to instar that do not exist in qemu-img.

| Document | Description |
|----------|-------------|
| [Configuration Guide](/components/instar/configuration/) | Command-line flags, config files, quirk control |
| [Chain Discovery](/components/instar/chain-discovery/) | `instar info --chain` - secure backing chain discovery |
| [Chain Config Protocol](/components/instar/chain-config/) | Chain config structure layout and VMM-to-guest data flow |
| [Measure](/components/instar/measure/) | `instar measure` - predict file size for a target format |
| [Create](/components/instar/create/) | `instar create` - create a new empty disk image |
| [Resize](/components/instar/resize/) | `instar resize` - change a disk image's virtual size |
| [Rebase](/components/instar/rebase/) | `instar rebase` - change an overlay's backing-file reference |
| [Commit](/components/instar/commit/) | `instar commit` - merge an overlay's data into its backing |
| [Map](/components/instar/map/) | `instar map` - emit the allocation map of a disk image |
| [Snapshot](/components/instar/snapshot/) | `instar snapshot` - manage internal qcow2 snapshots |
| [Amend](/components/instar/amend/) | `instar amend` - change qcow2 image options (compat version, lazy_refcounts) |
| [Dd](/components/instar/dd/) | `instar dd` — windowed block copy (qemu-img dd compatible) |

## Compatibility

| Document | Description |
|----------|-------------|
| [Output Formats](/components/instar/output-formats/) | qemu-img output formats (human, JSON) and version profiles |
| [qemu-img Quirks](/components/instar/quirks/) | Known differences between `instar` and qemu-img output |
| [Image Notes](/components/instar/image_notes/README/) | Test images and the quirks they exposed |

## Testing and Coverage

| Document | Description |
|----------|-------------|
| [Integration Testing](/components/instar/testing/) | Test suite comparing instar output against qemu-img |
| [Differential Fuzzing](/components/instar/testing/#differential-fuzzing) | Randomised instar vs qemu-img comparison |
| [Format Coverage](/components/instar/format-coverage/) | Comparison with oslo.utils format_inspector, test coverage gaps |

## Understanding the Codebase

| Document | Description |
|----------|-------------|
| [Commentary Index](/components/instar/commentary/index/) | Lions-style annotated walkthrough of the codebase |
| [Reading Order](/components/instar/commentary/reading-order/) | Which files to read, in what sequence, and what to look for |
| [Architectural Decisions](/components/instar/commentary/architectural-decisions/) | The *why* behind every major design choice |

## Design Decisions

| Document | Description |
|----------|-------------|
| [Why Rust](/components/instar/rust-rationale/) | Memory safety, bare-metal support, rust-vmm ecosystem |
| [Format Detection Safety](/components/instar/format-detection-safety/) | Why auto-detection is safe in instar's KVM sandbox |

## Platform Analysis

Analysis of how major virtualization platforms use qemu-img and handle disk images.

| Document | Description |
|----------|-------------|
| [Usage Analysis](/components/instar/usage/) | How oVirt, Proxmox, and OpenStack use qemu-img |
| [Security Vulnerabilities](/components/instar/security/) | CVE analysis for image handling across platforms |
| [Security Audits](/components/instar/security-audits/) | Audit results, unsafe code review, and standing security properties |

---

# Disk Image Format Specifications

## QCOW2 Format

Comprehensive documentation for the qemu Copy-On-Write version 2 format,
derived from qemu source code analysis.

| Document | Description |
|----------|-------------|
| [Format Specification](/components/instar/qcow2/qcow2-format/) | Header structure, feature flags, constants |
| [L1/L2 Tables](/components/instar/qcow2/qcow2-l1l2-tables/) | Address translation, cluster types, extended L2 |
| [Reference Counting](/components/instar/qcow2/qcow2-refcount/) | Refcount tables, variable widths, COW semantics |
| [Snapshots](/components/instar/qcow2/qcow2-snapshots/) | Snapshot table format, operations, VM state |
| [Compression](/components/instar/qcow2/qcow2-compression/) | ZLIB/ZSTD implementation, compressed entries |
| [Encryption](/components/instar/qcow2/qcow2-encryption/) | LUKS header, key slots, IV generation |
| [Implementation Notes](/components/instar/qcow2/qcow2-implementation-notes/) | Common pitfalls, validation, external refs |

## Raw Format

Documentation for raw disk images - the simplest format with no metadata.

| Document | Description |
|----------|-------------|
| [Format Specification](/components/instar/raw/raw-format/) | Structure, sparse files, tools, performance |

## VMDK Format

Documentation for VMware Virtual Machine Disk format.

| Document | Description |
|----------|-------------|
| [Format Specification](/components/instar/vmdk/vmdk-format/) | Header structures, magic numbers, disk types |
| [Extent Types](/components/instar/vmdk/vmdk-extents/) | Descriptor format, flat vs sparse, multi-extent |
| [Grain Tables](/components/instar/vmdk/vmdk-grain-tables/) | Address translation, GD/GT structure, COW |
| [Compression](/components/instar/vmdk/vmdk-compression/) | DEFLATE compression, streamOptimized format |

---

# Prototype and Research Documentation

The content below documents the prototyping and research phase of instar
development. These documents are retained for historical context and may
be useful for understanding design decisions, but the main implementation
has evolved beyond these prototypes.

## Prototypes

Experimental implementations exploring secure isolated execution.

| Prototype | Description |
|-----------|-------------|
| [KVM Hello World](/components/instar/prototypes/kvm-hello-world/) | Minimal bare-metal KVM guest proof-of-concept |
| [KVM Hello World 2](/components/instar/prototypes/kvm-hello-world2/) | Using vm-memory crate for safer memory management |
| [Virtio-Block](/components/instar/prototypes/virtio-block/) | Virtio-block device emulation with file copy |
| [Virtio-Block2](/components/instar/prototypes/virtio-block2/) | Virtio-block with protobuf messaging |
| [Virtio-Block3](/components/instar/prototypes/virtio-block3/) | Virtio-block with configurable sector sizes |
| [Virtio-Block4](/components/instar/prototypes/virtio-block4/) | Virtio-block with performance statistics |
| [Virtio-Block5](/components/instar/prototypes/virtio-block5/) | Virtio-block with ioeventfd/irqfd optimizations |
| [Virtio-Block6](/components/instar/../prototypes/virtio-block6/README/) | Sparse/dynamic output file support |
| [Pluggable](/components/instar/../prototypes/pluggable/README/) | Modular operation architecture with shared infrastructure |
| [Pluggable2](/components/instar/../prototypes/pluggable2/README/) | Separate binary loading for operations (minimal attack surface) |
| [Info](/components/instar/../prototypes/info/README/) | Image format detection (`qemu-img info` equivalent) |

## KVM Virtualization Research

Documentation for building custom VMMs using the Linux KVM API.

| Document | Description |
|----------|-------------|
| [KVM API Guide](/components/instar/prototypes/kvm/) | KVM ioctls, memory setup, x86-64 long mode, VM exits |
| [Performance Counters](/components/instar/prototypes/performance-counters/) | KVM statistics, perf events, resource limiting |

## Guest Data Transfer Research

Methods for transferring data into and out of bare-metal KVM guests.

| Document | Description |
|----------|-------------|
| [Comparison](/components/instar/prototypes/data-transfer-comparison/) | Trade-offs, rust-vmm crates, and recommendations |
| [Direct Memory](/components/instar/prototypes/data-transfer-direct-memory/) | Shared memory regions, coalesced I/O, completion signaling |
| [Virtio-vsock](/components/instar/prototypes/data-transfer-virtio-vsock/) | Socket-based communication with CID addressing |
| [Virtio-block](/components/instar/prototypes/data-transfer-virtio-block/) | Block device interface for sector-based transfers |
| [Other Mechanisms](/components/instar/prototypes/data-transfer-other/) | Custom MMIO device, Port I/O, ioeventfd, virtio-fs, VFIO, hypercalls |

## Development Tools

| Document | Description |
|----------|-------------|
| [Building with Docker](/components/instar/prototypes/building-with-docker/) | Build prototypes using Docker CLI without VSCode |

## Rust Crate Ecosystem

The [rust-vmm](https://github.com/rust-vmm/community) project provides
production-tested virtualization components used by Firecracker, crosvm, and
Cloud Hypervisor. These crates reduce virtio implementation effort by 70%+.

| Crate | Side | Purpose |
|-------|------|---------|
| [kvm-ioctls](https://crates.io/crates/kvm-ioctls) | VMM | Safe KVM API wrappers |
| [vm-memory](https://crates.io/crates/vm-memory) | VMM | Guest memory abstraction |
| [virtio-queue](https://crates.io/crates/virtio-queue) | VMM | Virtqueue implementation |
| [virtio-blk](https://github.com/rust-vmm/vm-virtio) | VMM | Block device parsing |
| [virtio-vsock](https://crates.io/crates/virtio-vsock) | VMM | Vsock packet handling |
| [virtio-drivers](https://crates.io/crates/virtio-drivers) | Guest | `no_std` virtio drivers |

See [Comparison](/components/instar/prototypes/data-transfer-comparison/#rust-crate-ecosystem) for details
on how these crates affect implementation complexity.

## Shared Crates

Reusable Rust crates for the `instar` project.

| Document | Description |
|----------|-------------|
| [guest-protocol](/components/instar/crates/guest-protocol/) | Protocol Buffers messaging for guest-VMM communication |

### Format Parsing Crates (`src/crates/`)

These `no_std` crates provide canonical format parsing implementations
shared across all guest operations, eliminating code duplication:

| Crate | Description | Used by |
|-------|-------------|---------|
| `qcow2` | QCOW2 header parsing, L1/L2 cluster lookup, decompression (feature-gated), refcount reading, backing file extraction | info, check, compare, convert |
| `raw` | MBR/GPT partition table detection | info |
| `vmdk` | VMDK4 binary header parsing, descriptor I/O and text parsing, grain directory/table reading, streamOptimized footer/marker handling, write helpers | info, check, convert |
| `vhd` | VHD/VPC footer and dynamic header parsing, BAT reading with sector-cached lookups, block-level data access, write helpers (footer, dynamic header, geometry) | info, check, compare, convert |
| `vhdx` | VHDX header/region table/metadata parsing with CRC-32C validation, GUID-based metadata lookup, 64-bit BAT reading with interleaved SB entries, output builders (file identifier, headers, region table, metadata, BAT) | check, compare, convert |
