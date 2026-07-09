# Reading Order

*In which order should you read the instar source code?*

Lions organised his UNIX commentary around the logical flow of the system
rather than alphabetical file order. We do the same here: the reading
order follows a request from the moment it enters the host-side binary
until a result comes back out. By the end, you will have read every
major module and understood how they connect.

**Prerequisites:** Read `docs/technology-primer.md` first. It covers KVM,
virtio, page tables, protection rings, and bare-metal execution -- all
background knowledge assumed below.

---

## Phase 1: The Host Side (VMM)

All files in this phase are under `src/vmm/src/`.

### Step 1: `main.rs` -- The entry point

**What you will find:** The `clap` argument parser that defines every
subcommand (`info`, `copy`, `check`, `compare`, `convert`). The `main()`
function that dispatches to the appropriate handler. The entire KVM
lifecycle: creating a VM, allocating guest memory, setting up page tables
and a GDT, loading the guest binaries, configuring the vCPU, and running
the main vCPU loop.

**What to pay attention to:**

- The constants at the top of the file define the guest memory layout.
  These are duplicated from `shared/src/lib.rs` because the VMM is a
  normal `std` Rust binary while `shared` is `no_std`. The comment "must
  match shared crate" appears frequently -- this is a deliberate design
  tension. The shared crate is the source of truth; the VMM duplicates
  values for practical reasons.

- The `setup_long_mode()` function is the most unusual code in the VMM.
  It configures the vCPU to start directly in 64-bit long mode, bypassing
  real mode and protected mode entirely. It writes page tables and a GDT
  into guest memory, sets CR0/CR3/CR4/EFER, and configures segment
  registers. This is what eliminates the need for a BIOS or bootloader.

- The vCPU run loop (`loop { match vcpu.run() { ... } }`) handles VM
  exits. The exit reasons you will see are: `IoOut` (serial port writes
  from the guest), `MmioWrite`/`MmioRead` (virtio MMIO from the guest),
  `Hlt` (guest halted -- operation complete), and `Shutdown` (triple
  fault or error). Each exit type dispatches to a handler.

- The function `write_operation_config()` serialises an operation-specific
  config struct (e.g. `InfoConfig`, `ConvertConfig`) directly into guest
  memory at `OPERATION_CONFIG_ADDR`. This is how the host passes
  parameters to the guest without any syscall or IPC mechanism.

**Read this file first** because it is the frame that holds everything
else together. It is also the largest single file in the codebase
(~1800 lines), so budget time accordingly.

### Step 2: `virtio/block.rs`, `virtio/mmio.rs` -- Device emulation

**What you will find:** The implementation of virtio-block devices using
the `virtio-queue` crate. The `VirtioBlockDevice` struct that manages
MMIO registers, virtqueue state, and request processing. The MMIO
read/write handlers that the vCPU run loop dispatches to on
`MmioRead`/`MmioWrite` exits.

**What to pay attention to:**

- The MMIO register layout follows the virtio 1.0 specification. Each
  device occupies a 4KB region starting at `MMIO_BASE`. Register offsets
  like `0x000` (MagicValue), `0x070` (QueueNotify), `0x100+`
  (device-specific config) are defined by the spec.

- When the guest writes to the QueueNotify register, the VMM processes
  all pending descriptors in the virtqueue. Each descriptor chain
  represents a virtio-blk request: a header (sector number + operation
  type), a data buffer, and a status byte.

- The virtio-block layer translates virtqueue requests into host file
  I/O. This is the security boundary in action: the VMM reads/writes raw
  bytes from files. It never interprets those bytes as image format data.

- The `validate_io_request()` method checks every I/O request before
  it reaches the backing store: sector must be within device capacity,
  the offset calculation uses `checked_mul` to prevent integer overflow,
  and the buffer size is capped at `MAX_IO_BUFFER_SIZE` (1 MB) to prevent
  OOM from a malicious guest descriptor. The `read_descriptor()` helper
  validates descriptor indices against the queue size.

### Step 3: `io_thread.rs` -- Threaded I/O

**What you will find:** The I/O thread that handles virtio-block
requests off the main vCPU thread when ioeventfd is enabled. The
`IoDevice` struct that wraps a file handle with sector-aligned I/O.

**What to pay attention to:**

- With ioeventfd, the guest's doorbell write does not cause a VM exit.
  Instead, KVM signals an eventfd. The I/O thread waits on that eventfd
  and processes requests asynchronously while the vCPU continues running.
  This is the key performance optimisation.

- `DeviceRole` distinguishes input (read-only) from output (writable)
  devices. This is enforced at the I/O thread level, not just in the
  guest. A compromised guest that tries to write to an input device will
  have the request rejected by the host.

### Step 4: `chain.rs` -- Backing chain discovery

**What you will find:** The host-side logic for discovering and
validating QCOW2 backing file chains. The `BackingChain` struct that
holds the ordered list of images. Path validation against a security
allowlist. Circular reference and depth limit checks.

**What to pay attention to:**

- Chain discovery runs *on the host side*, iteratively. The VMM runs
  `instar info` on each image in the chain (launching a fresh KVM guest
  each time) to extract the backing file path. This is critical for
  security: the host validates paths *before* opening files, so a
  malicious image cannot trick the guest into reading arbitrary host files.

- The `validate_backing_path()` function checks paths against an
  allowlist. Without this, a crafted QCOW2 backing file path like
  `../../../../etc/shadow` could be used for directory traversal.

### Step 5: `config.rs`, `error.rs`, `backing.rs`, `stats.rs`

**What you will find:** Supporting modules for configuration file
parsing, error types, backing store abstractions, and performance
statistics.

**What to pay attention to in `backing.rs`:** The `BackingStore` enforces
device capacity on every write via `checked_add` overflow protection and
an explicit `end > capacity` check. This is a defence-in-depth layer
behind the virtio-block validation -- even if the virtio layer were
bypassed, the backing store would reject writes beyond the image
boundary.

**Read the others lightly.** They exist to support the main flow and
contain few surprising design decisions.

---

## Phase 2: The ABI Contract (Shared)

The single file `src/shared/src/lib.rs` is the most important file in
the codebase after `vmm/main.rs`.

### Step 6: `shared/src/lib.rs` -- The contract

**What you will find:** Every type, constant, and address that both
the host-side VMM and the guest-side operations must agree on. The
`CallTable` struct (the guest's "syscall table"). All operation config
structs — one per subcommand (`InfoConfig`, `CopyConfig`,
`CheckConfig`, `CompareConfig`, `ConvertConfig`, `MeasureConfig`,
`CreateConfig`, `ResizeConfig`, `RebaseConfig`, `CommitConfig`,
`MapConfig`, `SnapshotConfig`). All result structs. Memory layout
constants with compile-time overlap assertions.

**What to pay attention to:**

- The `CallTable` is the central abstraction. It is a C-repr struct of
  function pointers that the core binary writes to a fixed memory address
  (`CALL_TABLE_ADDR = 0xF0000`). Operations read this struct to call
  back into the core for I/O. This is the guest's equivalent of a syscall
  table, except it is just a struct in memory -- no privilege transition
  needed because the guest has no privilege levels.

- Every struct is `#[repr(C)]` to ensure a stable ABI between separately
  compiled binaries. The core and operations are compiled independently
  and loaded at different addresses. They share no Rust-level linking;
  their only connection is the binary layout of these structs.

- The memory map constants (`CALL_TABLE_ADDR`, `OPERATION_CONFIG_ADDR`,
  `SCRATCH_MEM_BASE`, `STACK_BASE`, etc.) define the guest's physical
  address space. The `const _: () = assert!(...)` blocks are compile-time
  checks that regions do not overlap. This is a pattern worth noting:
  memory layout bugs in bare-metal code are catastrophic and hard to
  debug, so catching them at compile time is valuable.

- The `cached_read!` macro generates sector-cached read functions. Every
  format crate needs to read typed values from byte offsets within a
  virtio device. Reading a full sector for each 4-byte value would be
  wasteful, so the macro generates functions that cache the most recently
  read sector. This pattern appears in every format crate.

- The `bump_allocator!` macro provides heap allocation for operations
  that need `alloc` (ZSTD decompression, deflate compression). It is
  backed by a fixed address range in scratch memory, never frees, and
  can be reset to zero between logical operations. This is appropriate
  because the guest runs one operation and halts -- there is no long-
  running process that needs a general-purpose allocator.

### Step 6a: `shared/src/format_detection.rs` -- Magic number matching

**What you will find:** The `detect_format_from_header()` function that
identifies image formats by their magic numbers. Also `detect_vhd_footer()`
(VHD stores its magic at the *end* of the file) and ISO detection at a
non-zero offset.

**Why this is in shared:** Both the guest info operation and the host-side
chain discovery need format detection. Putting it in `shared` avoids
duplication.

### Step 6b: `shared/src/bitmap.rs` -- Overlap detection

**What you will find:** A 1-bit-per-unit bitmap used by the check
operation to detect cluster/grain/block overlaps. When two L2 entries
point to the same host cluster, this bitmap catches it.

---

## Phase 3: The Guest Side (Core)

### Step 7: `core/src/main.rs` -- Guest boot

**What you will find:** The `_start()` entry point that runs when the
vCPU begins executing. Device initialisation (creating `VirtioBlock`
instances by probing MMIO addresses). The `setup_call_table()` function
that writes function pointers to `CALL_TABLE_ADDR`. The `call_operation()`
function that jumps to the operation binary at `OPERATION_LOAD_ADDR`.

**What to pay attention to:**

- The `SingleThreadCell` wrapper is the guest's solution to Rust's
  requirement that statics be `Sync`. Since the guest runs on exactly
  one vCPU with no threads, a `Sync` wrapper around `UnsafeCell` is
  sound. This is well-documented in the code with safety comments.

- The call table setup populates every function pointer. The
  `verbose_print` pointer is conditionally set to either `ct_debug_print`
  or `ct_silent_print` (a no-op) based on the verbose flag in the
  operation config. This avoids serial I/O overhead when verbose output
  is not requested.

- After setting up the call table, the core calls `call_operation()`,
  which transmutes the `OPERATION_LOAD_ADDR` into a function pointer and
  calls it. This is how two separately compiled binaries (core + operation)
  cooperate at runtime. The operation binary's `_start` function is at the
  start of its loaded image.

- The `cstr_to_str()` function converts null-terminated C strings to Rust
  `&str`. It validates UTF-8 and has a length limit to prevent unbounded
  reads on unterminated strings. This defensive coding is important because
  the function is called with pointers from operation binaries that may be
  processing untrusted data.

### Step 7a: `core/src/virtio.rs` -- Guest-side virtio driver

**What you will find:** The `VirtioBlock` struct that implements the
guest side of the virtio protocol. MMIO register reads/writes, virtqueue
descriptor chain construction, and request submission.

**What to pay attention to:**

- This is the mirror image of `vmm/src/virtio.rs`. The VMM implements the
  device side; the core implements the driver side. Both must agree on the
  MMIO register layout, descriptor format, and request header structure.

- The DMA pool (`DMA_POOL_BASE`) is used for virtio request headers, data
  buffers, and status bytes. These must be in guest-physical memory that
  both the guest CPU and the VMM can access.

### Step 7b: `core/src/serial.rs` -- Guest-side serial protocol

**What you will find:** Functions for sending protobuf-encoded messages
over the serial port (`send_init`, `send_progress`, `send_error`,
`send_complete`, `send_info_result`, etc.). The `read_config()` function
that receives configuration from the VMM at startup.

**What to pay attention to:**

- The serial port is used for structured messages, not for general I/O.
  All messages are framed Protocol Buffer messages. The guest writes bytes
  to I/O port 0x3F8 (COM1) using x86 OUT instructions. The VMM receives
  them via `IoOut` VM exits.

---

## Phase 4: An Operation (Info)

Now that you understand the infrastructure, read a complete operation
to see how it all fits together.

### Step 8: `operations/info/src/main.rs` -- Format detection

**What you will find:** The `_start()` entry point for the info
operation. It reads the call table from `CALL_TABLE_ADDR`, gets the
operation config, reads sectors from the input device via the call table,
detects the image format, extracts metadata (virtual size, cluster size,
backing file, etc.), and sends results back via `send_info_result()`.

**What to pay attention to:**

- The operation is `#![no_std]` and `#![no_main]`. It has no standard
  library, no main function, and no runtime. It is a flat binary loaded
  at a fixed address.

- All I/O goes through the call table. `(call_table.read_input_sector)()`
  reads a sector; `(call_table.send_info_result)()` sends results. The
  operation never touches hardware directly -- it calls functions that
  the core provided.

- Format detection is a cascade of magic number checks. QCOW2 magic
  `0x514649fb` at offset 0, VMDK magic `0x564d444b` at offset 0, VHD
  magic `conectix` at the *end* of the file, etc. If no magic matches
  and unsafe quirks are disabled, the image must have a valid MBR or GPT
  partition table to be accepted as raw.

- For each detected format, the operation reads format-specific metadata.
  QCOW2 gets the full header parsed (version, cluster_bits, L1 table,
  incompatible features, header extensions for backing format and external
  data file). VMDK gets descriptor parsing. VHD/VHDX get footer/header
  parsing.

---

## Phase 5: A Format Crate (QCOW2)

### Step 9: `crates/qcow2/src/lib.rs` -- QCOW2 parsing

**What you will find:** Header constant definitions. The `read_cluster()`
function that does L1 → L2 → data lookup. Extended L2 subcluster support.
Compressed cluster decompression (zlib and ZSTD behind feature flags).
Refcount table reading. Backing chain walking via `read_virtual_offset()`.

**What to pay attention to:**

- This crate is used by *every* operation that touches QCOW2 data (info,
  check, compare, convert). It is the canonical implementation -- operations
  do not maintain their own QCOW2 parsing code.

- The L1/L2 lookup is the heart of QCOW2. A virtual offset is split into
  an L1 index, an L2 index, and an intra-cluster offset. The L1 table
  maps to L2 tables; each L2 entry maps to a host cluster. Extended L2
  entries are 16 bytes instead of 8, with subcluster allocation bitmaps.

- Compressed clusters require special handling. The cluster's host offset
  and compressed size are packed into the L2 entry differently from
  standard clusters. The compressed data may span sector boundaries, so
  it is read into `COMPRESSED_BUF_SIZE` (cluster size + one sector).

- The `read_virtual_offset()` function implements backing chain flattening.
  If a cluster is unallocated in the top image, it walks down the chain
  to find it in a backing image. Each backing image may be a different
  format (QCOW2, raw, VMDK, VHD), so the function dispatches to the
  appropriate reader based on the `ChainConfig` metadata.

- Checked arithmetic (`checked_mul`, `checked_add`) is used throughout
  for calculations involving untrusted header values. An integer overflow
  in an L1 table size calculation could cause an out-of-bounds read.

---

## Phase 6: The Convert Operation

### Step 10: `operations/convert/src/main.rs` -- Full pipeline

**What you will find:** The most complex operation. Reads virtual content
from any supported input format (with backing chain flattening and
decompression), writes to any supported output format (raw, QCOW2, VMDK,
VHD, VHDX). QCOW2 output involves writing headers, L2 tables, refcount
tables, and optionally compressed clusters. VMDK output uses a configurable
grain size (4KB-64KB via `ConvertConfig.output_grain_size`). VHD and VHDX
output use a configurable block size (via `ConvertConfig.output_block_size`)
with format-specific defaults and validation ranges.

**What to pay attention to:**

- The `ScratchLayout` struct computes buffer addresses at runtime based
  on the output cluster size. This is necessary because QCOW2 cluster
  sizes range from 512 bytes to 2MB, and the scratch memory region is
  shared among multiple buffers. Three conceptual buffers (header, L2
  table, refcount block) share a single "multipurpose" buffer because
  they are used in non-overlapping phases.

- QCOW2 output uses "linear cluster allocation with OFLAG_COPIED" -- the
  simplest possible allocation strategy. Clusters are written sequentially,
  each L2 entry gets OFLAG_COPIED set (meaning refcount is exactly 1),
  and refcount metadata sizing uses iterative convergence because the
  refcount tables themselves consume clusters that need refcounting.
  The `--extended-l2` flag switches from 8-byte to 16-byte L2 entries
  with subcluster allocation/zero bitmaps. This halves the entries per
  L2 table (requiring more L1 entries) and sets `incompatible_features`
  bit 4 in the output header. Written data clusters have their subcluster bitmaps computed by
  scanning each 2 KiB range for zeros (`compute_subcluster_bitmap()`);
  compressed clusters use a zero bitmap per the QCOW2 spec. The `--luks-encrypt-passphrase`
  flag enables LUKS-encrypted output (crypt_method=2). The VMM generates
  random key material and passes it to the guest at a dedicated memory
  region. The guest builds a LUKS v1 header (PBKDF2 + AFsplitter +
  AES-XTS key wrapping), writes it to clusters 1-K, encrypts each data
  cluster with AES-XTS, and stores an EXT_ENCRYPT_HEADER extension
  pointer in the QCOW2 header.

- The `bump_allocator!()` macro provides heap allocation for compression
  (miniz_oxide) and ZSTD decompression (ruzstd). The heap is reset
  (`HEAP_POS.store(0, ...)`) between clusters to prevent exhaustion.

---

## Phase 7: The Test Infrastructure

### Step 11: `tests/` -- Integration tests

**What you will find:** Python tests using `testtools` and `stestr`. The
test base class in `base.py`. The manifest (`manifest.json`) that defines
test images with their expected properties. Tests compare instar output
against `qemu-img` output to verify drop-in compatibility.

**What to pay attention to:**

- Tests are organised by operation: `test_info.py`, `test_check.py`,
  `test_compare.py`, `test_convert.py`, `test_security.py`,
  `test_adversarial.py`, `test_cve_reproduction.py`.

- Safety levels control which tests run: `safe` (default), `caution`
  (edge cases), `malicious` (CVE reproducers, opt-in only).

- The oslo.utils cross-validation tests (`test_oslo_crossval.py`) verify
  that instar's format detection agrees with OpenStack's `format_inspector`.
  This catches drift between the two implementations.

### Step 12: `src/fuzz/` -- Coverage-guided fuzzing

**What you will find:** A `cargo-fuzz` (libFuzzer) project with 13 fuzz
targets that exercise the `no_std` parser crates directly, without the
VMM/KVM stack. The harness module (`src/lib.rs`) provides a mock
`CallTable` backed by thread-local fuzzer input. Fuzz targets are split
into buffer-based (header parsing) and CallTable-dependent (L1/L2 lookup,
refcount traversal, decompression, BAT/grain lookup).

**What to pay attention to:**

- The mock `CallTable` in `src/fuzz/src/lib.rs` replaces real sector I/O
  with reads from the fuzzer input buffer. This is the key abstraction
  that decouples parsers from the VMM for fuzzing.

- Buffer-based targets (e.g. `fuzz_qcow2_header`) call `parse()` methods
  directly with the fuzz input as `&[u8]`. No CallTable needed.

- CallTable-dependent targets (e.g. `fuzz_qcow2_l1l2`) initialise parser
  state via the mock CallTable, then exercise lookup functions at both
  fixed and fuzz-derived offsets.

- The corpus seeding script (`scripts/extract-fuzz-corpus.py`) extracts
  seed images from the separate `instar-testdata` repository, filtered by
  format. No adversarial images are stored in the main repo.

- The CI workflow (`coverage-fuzz.yml`) runs nightly and on PRs that touch
  fuzz/parser code. Crashes are minimised and filed as GitHub Issues
  immediately.

### Step 13: `scripts/differential-fuzz.py` -- Differential fuzzing

**What you will find:** A Python script that generates random valid images
and compares instar output against `qemu-img` (and optionally libyal tools).
This is complementary to coverage-guided fuzzing: differential fuzzing
explores valid image space, while coverage fuzzing explores malformed input
space.

### Step 14: `.github/workflows/fuzz-autofix.yml` -- Automated bug fixes

**What you will find:** A CI workflow that closes the loop on fuzzer
findings. It picks up open `security-audit` issues (filed by the coverage
and differential fuzzers), invokes Claude Code to diagnose and fix the
crash, verifies the fix by rebuilding and running tests, and creates a PR.

**What to pay attention to:**

- Two fix attempts per issue. The second attempt receives the diff and
  failure output from the first as additional context.

- Complexity guardrails: 30-turn limit, max 3 source files changed, no
  cross-crate changes, no new dependencies. Issues that exceed these
  limits are labelled `autofix-complex` for human attention.

- The workflow uses `--body-file` for issue comments and PR bodies to
  avoid YAML escaping issues with markdown in shell heredocs.

---

## Summary: The Complete Data Flow

To trace a single `instar info image.qcow2` from start to finish:

1. **VMM `main()`** parses CLI args, opens `image.qcow2`
2. **VMM** creates a KVM VM, allocates 32MB guest memory
3. **VMM** writes page tables and GDT into guest memory
4. **VMM** loads `core.bin` at `0x10000`, `info.bin` at `0x30000`
5. **VMM** writes `InfoConfig` to `0xF1000` in guest memory
6. **VMM** creates virtio-block device backed by `image.qcow2`
7. **VMM** configures vCPU (long mode, stack, entry point) and runs it
8. **Core `_start()`** initialises virtio devices, sets up call table
9. **Core** jumps to `info.bin` at `0x30000`
10. **Info `_start()`** reads sector 0 via call table, detects QCOW2 magic
11. **Info** parses QCOW2 header, extracts virtual size, backing file, etc.
12. **Info** calls `send_info_result()` via call table
13. **Core** sends protobuf message over serial port (x86 OUT)
14. **VMM** receives bytes via `IoOut` VM exit, decodes protobuf
15. **Info** returns; core calls `send_complete()`, then `HLT`
16. **VMM** sees `Hlt` exit, formats output, prints to stdout
