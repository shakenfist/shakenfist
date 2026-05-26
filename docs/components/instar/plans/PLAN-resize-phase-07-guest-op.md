# PLAN-resize phase 7: guest `resize` operation

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (KVM guest binary constraints, virtio-block,
on-disk format layouts), research as needed to give a confident
answer. Flag any uncertainty explicitly rather than guessing.

This is a phase plan under `PLAN-resize.md`. Refer to that master
plan for overall context. Phases 1–6 shipped the planner crate
plus per-format planners (raw, qcow2 grow+shrink, vhd, vhdx,
vmdk monolithicSparse). Phase 7 makes the planner reachable
from the VMM by wiring up the guest binary that actually runs
inside KVM, applies the patches via the call table, and reports
results.

## Mission

1. Extend the call-table ABI in `src/shared/src/lib.rs` with two
   new function pointers:
   - `read_output_sector(u64, *mut u8, usize) -> bool` — first
     consumer is resize, which has to read the file it writes to.
     `rebase` and `commit` will reuse it later.
   - `send_resize_result(*const ResizeResult)` — analogous to
     `send_create_result`.
   Both go at the *end* of `CallTable` to preserve backward
   compat with previously-built guest binaries.

2. Extend `crates/guest-protocol/proto/guest.proto` with a
   `ResizeResultMessage` (fields mirror `CreateResultMessage`'s
   shape, replacing the create-specific fields with resize's
   `resolved_new_virtual_size` / `file_size_before` /
   `file_size_after` / `action` / `error`) and add it as field
   `12` in the `GuestMessage` oneof.

3. Land a new `src/operations/resize/` guest binary that:
   - Reads `ResizeConfig` from `OPERATION_CONFIG_ADDR`.
   - Reads sector 0 of the output device (via the new
     `read_output_sector`) and dispatches on the detected format.
   - Runs the format-specific pre-pass that stages every byte
     range the planner's opts struct needs (the existing L1 /
     refcount-table / refcount-block snapshots for qcow2; the
     footer / dynamic header / BAT for vhd; the active header /
     region table / metadata / BAT for vhdx; the header /
     descriptor / GD for vmdk).
   - Calls the matching `crates/resize::plan_resize_*`.
   - Applies the returned `ResizePlan` patches via
     `write_output_sector`, handling partial-sector writes via
     read-modify-write using `read_output_sector`.
   - Sends a `ResizeResult` via `send_resize_result`.

4. Wire the new binary into the workspace and build scripts:
   - `src/Cargo.toml` `members` list.
   - `src/build.sh` (compile + copy + size-check).
   - `scripts/check-binary-sizes.sh` op list.
   - `Makefile`'s `CARGO_TOML_FILES` for release bumps.

The 384 KiB binary size cap applies. Pulling in the full
planner crate (which itself depends on qcow2/vhd/vhdx/vmdk
parsers + the qcow2 `create` feature) is a real risk; the size
check is one of the success criteria.

## What the survey turned up

- **`src/operations/create/src/main.rs`** is the structural
  template: `_start()` reads CallTable from `CALL_TABLE_ADDR`,
  validates magic, reads CreateConfig from
  `OPERATION_CONFIG_ADDR = 0x81000`, dispatches per-format,
  sends result via `(call_table.send_create_result)(&result)`.
- **Linker / Cargo setup**: each operation has a `linker.ld`
  (load at `OPERATION_BASE = 0x20000`, cap 384 KiB), a
  `.cargo/config.toml` (target `x86_64-unknown-none` +
  linker-script injection), and a `Cargo.toml` with
  `panic = "abort"`, `lto = true`, `opt-level = "z"`.
- **CallTable struct** at `src/shared/src/lib.rs:498-676`:
  per-device input I/O, output I/O (write only currently),
  config getters, per-operation result senders. Result senders
  are appended (not reordered) to keep older guest binaries
  loadable against newer hosts; phase 7 appends two new
  pointers at the end.
- **`format_detection::detect_format_from_header`** at
  `src/shared/src/format_detection.rs:60` returns an
  `ImageFormat` enum value from the magic in sector 0.
- **Protobuf** at `crates/guest-protocol/proto/guest.proto`:
  proto3, package `guest`, `GuestMessage` oneof with create_result
  at field 11. Phase 7 adds `ResizeResultMessage` and assigns it
  field 12. Field-number stability matters — guest-side encoded
  messages are decoded host-side by the same proto definition.
- **Build script** at `src/build.sh`: builds each operation in
  turn, then copies its `.bin` to `target/release/`, then runs
  the size check. Phase 7 adds a `resize` block mirroring the
  existing create / measure blocks.
- **Size check** at `scripts/check-binary-sizes.sh:65`: a
  hardcoded list of operation names. Phase 7 adds `resize`.

## Algorithmic design

### Call-table extensions

```rust
// src/shared/src/lib.rs — appended to CallTable

/// Read a sector from the *output* device. Resize (the first
/// consumer) reads the file it writes to.  Future operations
/// like rebase and commit will reuse this.
pub read_output_sector:
    unsafe extern "C" fn(u64, *mut u8, usize) -> bool,

/// Send the resize result to the host.
pub send_resize_result: unsafe extern "C" fn(*const ResizeResult),
```

Both added *after* `send_create_result` so the byte layout
preserves the order older operation binaries depend on.

### Protobuf extensions

```protobuf
// crates/guest-protocol/proto/guest.proto

message ResizeResultMessage {
  string target_format = 1;
  uint64 resolved_new_virtual_size = 2;
  uint64 file_size_before = 3;
  uint64 file_size_after = 4;
  // "noop" | "grow" | "shrink"
  string action = 5;
  uint32 error = 6;
}

message GuestMessage {
  // ... existing fields ...
  oneof payload {
    // ... existing oneof entries 2-11 ...
    ResizeResultMessage resize_result = 12;
  }
}
```

### Guest binary layout

```
src/operations/resize/
├── .cargo/config.toml      # target = x86_64-unknown-none + linker
├── Cargo.toml              # deps: shared, resize, qcow2 (+create
│                           #   feature), vhd, vhdx, vmdk, raw
├── linker.ld               # load at 0x20000; cap 384 KiB
└── src/
    └── main.rs             # _start, panic_handler, per-format
                            # pre-pass + dispatch + patch
                            # applicator
```

`main.rs` follows the create/measure template:

```text
#![no_std]
#![no_main]

#[panic_handler] fn panic(_) -> ! { loop {} }

const SCRATCH_BASE: usize = 0x100000;          // wherever phase 1/2
                                               // create-style scratch starts
static mut HEADER_BUF: [u8; MAX_SECTOR_SIZE] = ...;
static mut RESIZE_SCRATCH: [u8; QCOW2_MAX_RESIZE_SCRATCH] = ...;

#[no_mangle]
pub unsafe extern "C" fn _start() -> ! {
    let call_table = &*(CALL_TABLE_ADDR as *const CallTable);
    validate_call_table!(call_table);

    let cfg = &*(OPERATION_CONFIG_ADDR as *const ResizeConfig);
    if !cfg.is_valid() { send_err(ERROR_INVALID_OPTION); halt(); }

    // 1. Read sector 0 from the output device.
    (call_table.read_output_sector)(0, HEADER_BUF.as_mut_ptr(),
                                    HEADER_BUF.len());
    let format = detect_format_from_header(&HEADER_BUF[..], 512, false);

    // 2. Dispatch on format → run the pre-pass → call planner →
    //    apply patches.
    let result = match format {
        ImageFormat::Raw   => run_raw(cfg, call_table),
        ImageFormat::Qcow2 => run_qcow2(cfg, call_table),
        ImageFormat::Vhd   => run_vhd(cfg, call_table),
        ImageFormat::Vhdx  => run_vhdx(cfg, call_table),
        ImageFormat::Vmdk3 | ImageFormat::Vmdk4 => run_vmdk(cfg, call_table),
        _ => err_result(ERROR_UNSUPPORTED_FORMAT),
    };

    (call_table.send_resize_result)(&result);
    halt();
}
```

### Per-format pre-pass

Each `run_<fmt>` function:
1. Reads the per-format metadata regions that the planner's
   opts struct needs (e.g. for qcow2: the L1 table cluster,
   the refcount-table cluster, the refcount blocks covering
   the new-cluster span).
2. Constructs the `*ResizeOpts` struct pointing at staged
   slices.
3. Calls the matching `plan_resize_*` function.
4. Applies the returned `ResizePlan`.
5. Returns a populated `ResizeResult`.

For phase 7 the pre-pass and applicator share a scratch buffer
sized for the worst-case format. The qcow2 worst-case
(`QCOW2_MAX_RESIZE_SCRATCH = 32 MiB`) dominates. The binary
size cap is on the .text+.rodata; static mut scratch buffers
live in `.bss` and don't count toward the binary's on-disk
size, only the runtime memory map. Verify this assumption with
`make check-binary-sizes`.

### Patch applicator

```text
fn apply_plan(call_table: &CallTable, plan: &ResizePlan) -> Result<(), u32> {
    for patch in plan.patches() {
        match patch {
            Write { byte_offset, bytes }
            | Append { byte_offset, bytes } => {
                write_byte_range(call_table, byte_offset, bytes)?;
            }
            ZeroFill { byte_offset, len } => {
                zero_byte_range(call_table, byte_offset, len)?;
            }
        }
    }
    Ok(())
}
```

`write_byte_range` is the workhorse. Patches are byte-aligned
but the call-table works in sectors. The naive approach:

1. Walk the byte range sector by sector.
2. For the first / last sector of the range (which may be
   partially overlapping), read the existing sector via
   `read_output_sector`, splice the patch bytes into the
   right offsets, write back via `write_output_sector`.
3. For sectors fully within the range, write directly from
   the patch bytes.

`zero_byte_range` is similar but reuses a single 512-byte zero
buffer.

For Append patches that extend the file beyond its current
EOF, `write_output_sector` is expected to grow the file
implicitly — the same behaviour create relies on for raw
images. The host's post-pass `set_len(plan.total_file_size)`
trims any over-allocated tail (or extends to exact length if
the last patch didn't reach EOF).

### Result construction

```rust
fn build_result(
    cfg: &ResizeConfig,
    plan: Result<ResizePlan, ResizeError>,
    file_size_before: u64,
) -> ResizeResult {
    match plan {
        Ok(p) => ResizeResult {
            magic: ResizeResult::MAGIC,
            target_format: cfg.target_format,
            resolved_new_virtual_size: cfg.new_virtual_size,
            file_size_before,
            file_size_after: p.total_file_size,
            action: encode_action(p.action),
            error: ResizeResult::ERROR_OK,
        },
        Err(e) => ResizeResult {
            magic: ResizeResult::MAGIC,
            target_format: cfg.target_format,
            resolved_new_virtual_size: 0,
            file_size_before,
            file_size_after: 0,
            action: ResizeResult::ACTION_NOOP,
            error: map_error(e),
        },
    }
}
```

`map_error` translates `ResizeError` variants to the matching
`ResizeResult::ERROR_*` constants (already defined in phase 1b).

### Crash safety at the operation boundary

The guest applies patches in plan order. The plan's
prepare → header / commit / cleanup partition (per phase 2c /
3b / 4b / 5b / 6b) is preserved by the applicator emitting
patches in slice order. The applicator does NOT batch or
reorder.

Filesystem-level atomicity is the kernel's responsibility;
the guest does single-sector writes that the host's virtio
layer turns into normal `write()` syscalls. A crash mid-plan
leaves the file in the format-specific intermediate state
each per-format planner documents (qcow2's "before atomic
header swap → file looks pre-resize", vhdx's "before
sequence-number bump → file looks pre-resize", etc.).

## Test surface

Phase 7's testable surface is small because the guest binary
needs the VMM to launch it. The phase's success criteria are
mostly build-time:

- `make instar` succeeds with the new operation in the build
  list.
- `make check-binary-sizes` passes — `resize.bin` fits under
  the 384 KiB cap.
- `make lint` clean.
- `pre-commit run --all-files` clean.

End-to-end behaviour is covered in phase 8's
`tests/test_resize.py` integration suite.

The applicator's sector-arithmetic logic *can* be unit-tested
in isolation — extract it into a helper that takes a mock
"writer" trait and assert it issues the right per-sector
writes for byte-aligned and byte-misaligned patches. Optional
follow-up; not required for phase 7's success criteria.

## Public API delta

No `crates/resize` surface change. The new ABI lives in
`shared`:

```rust
pub struct CallTable {
    // ... existing fields, in their current order ...
    pub send_create_result: unsafe extern "C" fn(*const CreateResult),
    /// Read a sector from the output device. New in phase 7.
    pub read_output_sector:
        unsafe extern "C" fn(u64, *mut u8, usize) -> bool,
    /// Send the resize result. New in phase 7.
    pub send_resize_result: unsafe extern "C" fn(*const ResizeResult),
}
```

Older guest binaries will read garbage if they touch the two
new fields, but they never reference them (they were compiled
against a CallTable struct that didn't have them). The host's
`CallTable` provider populates both fields with valid pointers
on every launch — older binaries simply don't see them in
their compiled struct layout.

## Open questions

1. **`SCRATCH` placement and size.** The qcow2 worst-case
   scratch is 32 MiB. `.bss` lives at runtime — the binary
   header doesn't carry .bss, only `.text + .rodata` count
   toward the 384 KiB cap. *Recommendation*: confirm by
   inspecting create's `static mut` declarations and verifying
   `objdump -h create.bin` reports a tiny .bss size on disk.

2. **Reading the output device's sector size**. Some formats
   support 4096-byte sectors. Resize calls
   `(call_table.get_output_sector_size)()` to learn the
   value. The per-format pre-passes pass the right
   logical_sector_size to the planner opts.

3. **Cross-validation against `current_virtual_size`**. The
   planner already does this and returns `HeaderMismatch` on
   disagreement. The guest does no extra validation; it just
   surfaces the planner's error.

4. **Partial-sector writes via read-modify-write**. The
   patches that are smaller than a sector (qcow2 refcount-
   entry writes, vmdk's 8-byte capacity, vhdx's 8-byte
   VirtualDiskSize) require: read the existing sector → splice
   in the patch bytes → write the sector back. The applicator
   handles this transparently.

5. **What happens if the read_output_sector pointer is null?**
   Should only happen if the host's CallTable provider is
   broken. The guest fails closed: validate_call_table tests
   only the magic, so we add a runtime null-pointer check at
   first use and surface `ERROR_READ_FAILED`.

6. **Backward compatibility with older `core.bin`**. The
   guest is loaded by core.bin; both are built together in
   `src/build.sh`. They share the same CallTable layout for a
   given build. The cross-version compatibility story applies
   only across releases (older operation binaries against
   newer hosts), which we don't ship from this branch.

7. **VMDK's MAX_SECTOR_SIZE constraint**. The header read
   buffer is sized to `MAX_SECTOR_SIZE` (4096 typically). VMDK
   supports up to 8192-byte sectors in theory — confirm via
   `vmdk::MAX_SECTOR_SIZE` whether the existing constant
   already covers it.

8. **Whether to also surface progress messages**. The existing
   create operation calls `send_progress` periodically. Resize
   typically has very few patches (≤128) so the per-patch
   percent computation isn't meaningful. *Recommendation*:
   omit progress reporting; the operation completes near-
   instantly.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | medium | sonnet | none | Extend the call-table ABI and the protobuf. (1) In `src/shared/src/lib.rs`, append two new function-pointer fields to `CallTable` after `send_create_result`: `pub read_output_sector: unsafe extern "C" fn(u64, *mut u8, usize) -> bool` and `pub send_resize_result: unsafe extern "C" fn(*const ResizeResult)`. Update the `CallTable::is_valid` (or equivalent) test as needed. (2) In `crates/guest-protocol/proto/guest.proto`, add `ResizeResultMessage` mirroring `CreateResultMessage`'s shape with these fields: `string target_format = 1; uint64 resolved_new_virtual_size = 2; uint64 file_size_before = 3; uint64 file_size_after = 4; string action = 5; uint32 error = 6;`. Add `ResizeResultMessage resize_result = 12;` to the `GuestMessage` oneof. (3) Run any protoc-equivalent build steps and verify the generated Rust binds compile. Add a unit test in `src/shared/src/lib.rs` asserting `mem::size_of::<CallTable>()` is within a forward-compat budget (or matches the expected layout). No callers yet — phase 8's host wiring populates the new function pointers; phase 7c uses them. `make instar` builds, `make lint` clean, `pre-commit run --all-files` clean. |
| 7b | medium | sonnet | none | Scaffold the resize operation binary and wire the build. (1) Create `src/operations/resize/` mirroring the layout of `src/operations/create/`: copy `Cargo.toml`, `linker.ld`, `.cargo/config.toml`; rename the binary to `resize` in Cargo.toml. The Cargo.toml's `[dependencies]` should include `shared`, `resize` (from `../../crates/resize`), plus the per-format crates (`qcow2 with "create" feature`, `vhd`, `vhdx`, `vmdk`) plus `raw`. (2) Add a minimal `src/main.rs` with `#![no_std] #![no_main]`, a panic handler, and a `_start` function that just constructs a `ResizeResult` with `ERROR_UNSUPPORTED_FORMAT` and calls `send_resize_result`. This validates the build pipeline without committing to the full implementation. (3) Add `"operations/resize"` to `src/Cargo.toml`'s `members` list. (4) Update `src/build.sh` to add a resize build block (mirror the create block), copy `resize.bin` to `target/release/`, and add it to the size-check loop / help text. (5) Add `resize` to the operation list in `scripts/check-binary-sizes.sh`. (6) Add `src/operations/resize/Cargo.toml` to the `CARGO_TOML_FILES` list in the Makefile. Verify `make instar` succeeds, `make check-binary-sizes` reports `resize.bin` under 384 KiB, `make lint` clean. |
| 7c | high | opus | worktree | Implement the full resize guest operation in `src/operations/resize/src/main.rs`. Replace the 7b stub with: (1) `_start` reads CallTable, validates magic, reads ResizeConfig from `OPERATION_CONFIG_ADDR`, validates its magic. (2) Reads sector 0 from the output device via `(call_table.read_output_sector)(0, ...)`, then calls `detect_format_from_header`. (3) Dispatches to a per-format `run_<fmt>(cfg, call_table) -> ResizeResult` function. Implement run_raw (returns NoOp since raw is host-only — but we should never reach here because the host shortcuts raw), run_qcow2 (read full L1 + refcount table + the relevant refcount blocks + for shrink the relevant L2 tables → populate Qcow2ResizeOpts → call plan_resize_qcow2 → apply), run_vhd (footer + dynamic header + BAT → VhdResizeOpts → plan_resize_vhd → apply), run_vhdx (active header + region table + metadata + BAT → VhdxResizeOpts → plan_resize_vhdx → apply), run_vmdk (header + descriptor + GD → VmdkResizeOpts → plan_resize_vmdk → apply). (4) Implement `apply_plan` and its helpers `write_byte_range` (sector arithmetic + read-modify-write for partial sectors via `read_output_sector`) and `zero_byte_range`. (5) Implement `map_error` translating ResizeError variants to ResizeResult::ERROR_* constants. (6) The guest must surface every patch correctly — the planner's partition invariant (prepare → header → cleanup) is preserved by emitting patches in slice order; the applicator does NOT reorder. Verify the binary still fits the 384 KiB cap after this implementation lands. `make instar`, `make check-binary-sizes`, `make lint`, `pre-commit run --all-files` clean. Risky: worktree isolation. |

## Out of scope for phase 7

- Host CLI / VMM wiring (phase 8). Phase 7 builds the guest
  binary but never launches it.
- Preallocation modes (phase 9). Phase 7 emits whatever the
  planner returns; the host-side preallocation pass layers on
  top in phase 8/9.
- Integration tests at the end-to-end level (phase 11).
- Cross-version baselines (phase 10).
- Documentation (phase 13).

## Success criteria for phase 7

- `cargo build -p resize` (the planner crate) still compiles.
- `cargo build -p resize-op` (the new operation binary) builds.
- `make instar` builds the full instar binary including
  `resize.bin`.
- `make check-binary-sizes` passes; `resize.bin` is reported
  and fits the 384 KiB cap.
- `make lint` clean across the workspace.
- `pre-commit run --all-files` clean.
- `make test-rust` passes (no behaviour regressions in the
  planner crate or shared/).
- Inspect the generated `CallTable` layout via the
  `mem::size_of` assertion added in 7a — confirms the two new
  fields land at the expected offsets and nothing else moved.
- The protobuf `ResizeResultMessage` decoder on the host side
  (not landed yet, but the regenerated bindings exist after
  7a's protoc step) compiles successfully.

## Sub-agent guidance

Read these files before starting any step:

- `src/operations/create/src/main.rs` (structural template).
- `src/operations/create/{Cargo.toml,linker.ld,.cargo/config.toml}`.
- `src/shared/src/lib.rs:498-676` (CallTable layout).
- `src/shared/src/format_detection.rs` (format dispatch helper).
- `crates/guest-protocol/proto/guest.proto` (proto template).
- `src/build.sh` (the build script being extended).
- `scripts/check-binary-sizes.sh` (the size-cap enforcer).
- `src/crates/resize/src/lib.rs` (the planner surface phase 7c
  consumes).

For each step the management session will:

- Read the actual files (not just trust the diff summary).
- Run `make instar`, `make check-binary-sizes`, `make lint`,
  `pre-commit run --all-files`.
- For 7c: also confirm the `resize.bin` file size hasn't
  regressed compared to the 7b stub by more than a reasonable
  margin (the planner pulls in qcow2/vhd/vhdx/vmdk parsers,
  but `lto = true` should let dead-code elimination keep the
  size manageable).
- Confirm the success-criteria items above.
- Commit if green with the standard commit-message format.

If the binary blows the 384 KiB cap in 7c, the worktree
isolation lets us discard and reattempt with selective parser
features (e.g. disable the qcow2 `create` feature in the
operation Cargo.toml if it's not strictly needed by the
planner's runtime path).
