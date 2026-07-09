# Architectural Decisions

*Why is instar built the way it is?*

Every non-trivial codebase embodies dozens of decisions that are obvious
to the people who made them and invisible to everyone else. This document
records the reasoning behind instar's major architectural choices. Where
a decision was driven by constraints or tradeoffs, we explain what was
considered and why the chosen path won.

---

## Decision 1: Bare-metal guest, not a unikernel or Linux VM

**The choice:** The guest runs as a flat binary on a single vCPU with
no kernel, no standard library, and no system call interface.

**Why not a Linux VM?** The whole point of instar is to isolate untrusted
format parsing. A Linux guest kernel is hundreds of thousands of lines
of code with its own history of exploitable bugs. If the guest kernel has
vulnerabilities, an attacker who compromises the parser could then
exploit the guest kernel to gain capabilities (networking, filesystem
access) that the isolation was supposed to prevent. A Linux guest also
takes seconds to boot, which is unacceptable for a tool that should feel
as fast as `qemu-img`.

**Why not a unikernel?** A unikernel would be smaller and faster than a
full Linux guest, but it still includes a library OS with a memory
allocator, a scheduler (even if trivial), and often a partial POSIX
layer. Each of these is code that could contain bugs. More practically,
unikernel frameworks (MirageOS, Unikraft, IncludeOS) each impose their
own language constraints, build system, and runtime model. Instar would
become dependent on a third-party framework rather than owning its entire
stack.

**Why bare metal works:** Instar's guest has extremely limited needs:
read sectors from a virtio device, write sectors, send messages over a
serial port, and do computation. It does not need processes, threads,
memory protection, a filesystem, or a network stack. A flat binary that
accesses MMIO registers directly can do everything the guest needs in
tens of kilobytes and boot in microseconds. The tradeoff is that you
must implement your own virtio driver and serial protocol, but those are
well-specified and straightforward.

---

## Decision 2: Two binaries (core + operation), not one

**The choice:** The guest consists of two separately compiled binaries.
`core.bin` handles device initialisation and the call table. The
operation binary (`info.bin`, `convert.bin`, etc.) is loaded at a
separate address and does the actual work.

**Why not a single binary?** Attack surface. If every operation were
compiled into a single monolithic guest binary, then running `instar info`
would load code for convert, check, and compare -- code that is not
needed and that increases the amount of code available to an attacker
who compromises the parser. By loading only the operation binary that is
needed, the guest contains the minimum code required for the current task.

**The mechanism:** The VMM loads `core.bin` at `0x10000` and the
operation binary at `0x30000`. Core initialises devices, writes a call
table to `0xF0000`, and then does a function call to `0x30000`. The
operation reads the call table and uses it for all I/O. The two binaries
share no Rust-level linking; their only contract is the `#[repr(C)]`
structs defined in the `shared` crate.

**The tradeoff:** This design requires careful ABI management. The call
table struct must have a stable binary layout, and both binaries must
agree on it. The `magic` and `version` fields in `CallTable` provide a
runtime check, and the shared crate provides a single source of truth
for types. But if someone changes a struct layout in `shared` without
recompiling both core and the operations, things will break silently.
The build system (`build.sh`, Makefile) always rebuilds all components
together to prevent this.

---

## Decision 3: Function pointers, not shared memory protocol

**The choice:** Operations communicate with the core via a table of
function pointers (`CallTable`), not a request/response protocol in
shared memory.

**Why not a shared memory ring buffer?** A ring buffer protocol would
require the operation to construct request messages, write them to
shared memory, and wait for responses. This adds latency (at least one
round trip per I/O) and complexity (message framing, flow control). Since
the core and operation run on the same vCPU in the same address space
with no memory protection, a function call is simpler and faster. The
operation calls `(call_table.read_input_sector)(0, sector, buf, len)`
and the function executes synchronously, returning when the data is
ready.

**Why this is safe:** The call table functions are `extern "C"` and
run in the same address space as the operation. There is no privilege
boundary to cross. If the operation corrupts the call table, it can only
harm itself -- the VMM is in a separate address space protected by KVM.

**The `extern "C"` ABI:** The function pointers use `extern "C"` calling
convention because the core and operations are compiled separately with
potentially different Rust compiler versions. The C ABI is stable across
compilations; the Rust ABI is not.

---

## Decision 4: Serial port for structured messages, virtio for data

**The choice:** Bulk data (disk sectors) flows through virtio-block
devices. Control messages (configuration, progress, results) flow
through the serial port.

**Why two channels?** Virtio-block is optimised for transferring large
amounts of data with minimal overhead (descriptor chains, ioeventfd,
batch processing). But it is not well suited to structured, variable-
length messages like "here is the format detection result." The serial
port, by contrast, is simple (x86 IN/OUT instructions), low-throughput,
and naturally suited to framed messages. Using the right mechanism for
each type of traffic keeps both paths simple.

**The protocol:** Control messages are Protocol Buffer (protobuf) encoded
and framed with a length prefix. The guest writes bytes to I/O port
0x3F8 (COM1). Each byte write causes a VM exit (`IoOut`), which is
expensive -- but control messages are small and infrequent compared to
data I/O, so this cost is acceptable. If control message throughput ever
became a bottleneck, a dedicated virtio-console or virtio-vsock device
could replace the serial port.

---

## Decision 5: Host-side chain discovery, not guest-side

**The choice:** Backing chain discovery (following QCOW2 `backing_file`
references) happens on the host side, not inside the guest.

**Why not discover the chain in the guest?** If the guest discovered
backing files, it would need to tell the VMM "please open this file and
give me a new virtio device for it." This creates a file-open oracle:
a malicious image could craft a backing file path like `../../etc/shadow`
and trick the VMM into opening arbitrary host files. Even with path
validation, the interaction pattern (guest requests file opens) is
inherently dangerous.

**How it works instead:** The VMM iteratively runs `instar info` on the
primary image to extract its backing file path. It validates the path
against a security allowlist *before* opening the file. Then it runs
`instar info` on the backing file, and so on until the chain is complete.
Each iteration launches a fresh KVM guest for isolation. Once the full
chain is known and validated, the VMM opens all the files itself and
presents them as multiple virtio-block devices (device 0 = top image,
device 1 = first backing file, etc.).

**The guest's role:** The guest receives a `ChainConfig` struct in
memory that describes the chain (format, virtual size, cluster size of
each device). It uses the call table to read from specific devices by
index. It never knows or cares about file paths.

**Exception for VMDK monolithicFlat descriptors:** A
monolithicFlat VMDK is an ASCII descriptor file plus a separate
raw flat extent file. The descriptor has no binary magic for the
guest's format detector to latch onto, and its only purpose is to
name the flat extent. Running it through the guest info operation
would mean either (a) teaching the guest about every two-file
format as a special case, or (b) producing a "format=raw" report
that discards the extent information. Instead, the VMM recognises
the `# Disk DescriptorFile` prefix on the host, parses the extent
line via `vmdk::parse_descriptor_extents` (the same alloc-free
parser the guest would use), runs the extent filename through
the existing backing-file allowlist, and opens the flat extent as
device 1. The `ChainConfig` on device 0 is then marked
`VmdkDescriptor` with `data_device_idx = 1`, letting guest
operations reuse the QCOW2 external-data-file redirect for
content reads. This keeps the guest's role unchanged (still just
reads devices by index) while acknowledging that a plain-text
two-file format is the one case where host-side parsing is
simpler and no less safe — the descriptor is pure ASCII with no
state, and every path it names still flows through the same
allowlist as a QCOW2 backing file.

---

## Decision 6: `no_std` format crates with feature flags

**The choice:** Format parsing crates (`qcow2`, `vmdk`, `vhd`, `vhdx`,
`luks`, `raw`) are `no_std` libraries with optional features behind
Cargo feature flags.

**Why `no_std`?** These crates run inside the bare-metal guest, which
has no standard library. They must compile without `std`. This also
means they cannot use `Vec`, `String`, `HashMap`, or `std::io` -- all
data structures are fixed-size, all I/O goes through function pointers
passed as arguments.

**Why feature flags?** Not every operation needs every capability. The
info operation needs header parsing but not decompression. The check
operation needs L2 table walking but not compressed cluster reading.
The convert operation needs everything. Feature flags (`decompress`,
`decompress-zstd`, `compress`, `vmdk-input`, `vhd-input`, etc.) allow
each operation to pull in only the code it uses, reducing binary size
and attack surface.

**The pattern:** A format crate exports public functions that take a
`&CallTable` and a device index. The function uses the call table to
read sectors from the device. This dependency-injection style keeps the
crates decoupled from the virtio layer -- they do not know they are
running in a KVM guest. They just read bytes through function pointers.

---

## Decision 7: Compile-time memory layout validation

**The choice:** Guest memory regions are defined as constants in
`shared/src/lib.rs`, and their non-overlap is verified with
`const _: () = assert!(...)` blocks.

**Why this matters:** In a bare-metal environment with no memory
protection, an overlap between the stack and the scratch memory region
(for example) would cause silent data corruption -- the most dangerous
class of bug. There is no MMU within the guest to catch it (the guest
uses identity-mapped pages). The compile-time assertions catch layout
errors before any code runs.

**What is checked:**

- Scratch memory does not overlap the DMA pool
- Scratch memory ends at least 64KB below the stack (guard gap)
- The allocator heap is within scratch memory
- Operation config does not overlap the call table

**What is not checked (and why):** The overlap between VMM-side constants
and shared-crate constants is not mechanically verified. The VMM is a
`std` binary and cannot depend on the `no_std` shared crate (or rather,
it duplicates the values). This is a known fragility; comments like "must
match shared crate" mark the duplication points.

---

## Decision 8: Iterative convergence for QCOW2 output metadata

**The choice:** When writing QCOW2 output, the convert operation
calculates the size of metadata (L1 table, L2 tables, refcount table,
refcount blocks) using iterative convergence rather than a closed-form
formula.

**Why iteration?** QCOW2 metadata is self-referential: the refcount
table tracks refcounts for all clusters, including the clusters that
hold the refcount table itself. If the image has N data clusters, you
need some number of refcount blocks (each tracking refcounts for M
clusters). But those refcount blocks are themselves clusters that need
refcounting. The refcount table that indexes the blocks is also one or
more clusters. And the L1 table, L2 tables, and header cluster all need
refcounting too.

A closed-form solution exists but is fiddly and error-prone (it depends
on cluster size, refcount width, and whether compression is enabled).
Iteration is simpler: start with a guess, calculate the metadata needed,
check if the metadata itself changes the count, and repeat until stable.
In practice, convergence happens in 2-3 iterations.

---

## Decision 9: Sector-cached reads, not bulk prefetch

**The choice:** Format crates use a one-sector cache for reads (the
`cached_read!` macro). If consecutive reads hit the same sector, only
one I/O is performed.

**Why not read multiple sectors at once?** The virtio-block protocol
supports multi-sector reads, but the guest's DMA pool is limited (64KB).
Reading many sectors at once would require larger buffers and more complex
buffer management. Since format metadata is often concentrated in a few
sectors (the QCOW2 header fits in one sector; L2 table entries are
adjacent), a one-sector cache captures most of the locality benefit with
zero buffer management complexity.

**When this is insufficient:** For compressed clusters that span multiple
sectors, the code reads the full compressed data into the
`COMPRESSED_BUF_SIZE` buffer in a dedicated loop. This is the only case
where bulk reads are needed, and it is handled explicitly rather than
through the general caching mechanism.

---

## Decision 10: qemu-img output compatibility as a hard requirement

**The choice:** Instar's output is byte-for-byte identical to `qemu-img`
for all supported operations (`info`, `check`, `compare`). The test
suite compares instar output against qemu-img output and fails on any
difference.

**Why?** Instar is intended as a drop-in replacement for `qemu-img` in
OpenStack and similar platforms. These platforms parse `qemu-img` output
programmatically. If instar's output differs in any way -- extra spaces,
different field order, different number formatting -- the integration
will break. Byte-for-byte compatibility means operators can switch from
`qemu-img` to `instar` without changing any parsing code.

**The cost:** This requirement drives complexity in the output formatting
code. Different versions of `qemu-img` produce slightly different output
(e.g., the "Child node '/file'" section appeared in qemu-img 8.0+).
Instar detects the installed qemu-img version and emits matching output.
This version detection logic is non-trivial but necessary for true
drop-in compatibility.

**Where we diverge intentionally:** The `--extra-detail` flag enables
instar-specific output (e.g., LUKS format detection) that qemu-img
does not support. The `--unsafe-quirks` flag matches qemu-img's less
secure behavior for compatibility testing. These are opt-in departures
from compatibility, not accidental differences.
