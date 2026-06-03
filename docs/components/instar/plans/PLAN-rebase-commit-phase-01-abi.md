# PLAN-rebase-commit phase 01: shared ABI

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (the `*Config` /
`*Result` family in `src/shared/src/lib.rs`, the `CallTable`
struct and its append-only convention, the `GuestMessage`
protobuf oneof in `crates/guest-protocol/proto/guest.proto`,
the chain-config plumbing in `src/vmm/src/main.rs`), and ground
your answers in what the code actually does today. Do not
speculate about the codebase when you could read it instead.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-rebase-commit-phase-NN-<descriptive>.md`. The master
plan is [PLAN-rebase-commit.md](/components/instar/plans/PLAN-rebase-commit/). This
phase is the first of twelve.

I prefer one commit per logical change, and at minimum one
commit per phase. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

This phase establishes the shared ABI surface that rebase and
commit will use. No planners, guest binaries, or host CLI
entry points are added yet — phases 2–9 build on top of what
phase 1 lands. The phase is intentionally small and mechanical
so it can ship in a single PR and unblock phases 2 and 6
(planners) to be developed in parallel.

The pattern is well-established by the most recent comparable
work (resize phase 7 in `PLAN-resize.md`):

- `*Config` and `*Result` structs live in
  `src/shared/src/lib.rs`, are `#[repr(C)]`, carry a 4-byte
  ASCII magic, and use an append-only error-code enum.
- `CallTable` function pointers for sending per-op results
  are appended at the end of the struct (see the comment at
  `src/shared/src/lib.rs:718` for the convention), and the
  `VERSION` constant bumps each time the struct layout
  changes.
- `GuestMessage` in `crates/guest-protocol/proto/guest.proto`
  is the wire format. New per-op result messages are
  appended both as new message types and as new arms in the
  `oneof payload` block.

### Master plan correction surfaced during this phase

The master plan's "Call-table extension" section says **none
required**, on the assumption that `read_output_sector`,
`write_output_sector`, `read_input_sector`,
`get_input_device_count`, `get_input_capacity`, and
`get_input_sector_size` together cover everything both
subcommands need.

Phase 1 research found a gap: commit's overlay-clear pass
needs to write the overlay's L2 / refcount tables, but the
overlay is attached as an *input* device (the backing it
writes into is the output). The current ABI has
`write_output_sector` and `read_input_sector` but no
`write_input_sector`.

Three options were considered:

1. **Two guest launches per commit** — first writes data into
   backing, second clears overlay metadata with overlay as
   output. No ABI change but doubles the per-commit guest
   startup overhead and complicates failure recovery (a
   crash between launches leaves the overlay metadata
   pointing at clusters that are now also in the backing).
2. **Swap the output role** — attach overlay as output (where
   metadata clear happens), attach backing as a writable
   input. Still needs `write_input_sector`. Same shape as
   option 3, just renamed.
3. **Add `write_input_sector` to `CallTable`** — symmetric
   counterpart of `read_input_sector`. Append-only, one new
   function pointer, bump `CallTable::VERSION` from 14 to 15.
   Host-side: extend `open_chain_devices` (or add a parallel
   helper) so that selected input slots can be opened
   `O_RDWR`; the host enforces that `write_input_sector` is
   only valid for those slots.

Phase 1 adopts option 3. This is the cleanest match for
`read_output_sector` (added in resize phase 7 by the same
"append-and-bump" pattern) and lets commit ship as a single
guest launch with the same atomicity story as resize.

The master plan must be updated in this phase to reflect this
correction (see step 1g).

## Mission and problem statement

After phase 1 lands:

1. `src/shared/src/lib.rs` defines four new structs:
   `RebaseConfig`, `RebaseResult`, `CommitConfig`,
   `CommitResult`, each with a unique magic, an explicit
   `error: u32` field with an initial set of error-code
   constants, and the per-format fields the operation will
   need at runtime. Each struct has an `is_valid()` helper
   and a `_reserved` padding tail for forward compatibility.

2. `CallTable` in `src/shared/src/lib.rs` gains three new
   function-pointer fields at the very end of the struct:
   `send_rebase_result`, `send_commit_result`, and
   `write_input_sector`. `CallTable::VERSION` is bumped from
   14 to 15.

3. `crates/guest-protocol/proto/guest.proto` gains two new
   message types (`RebaseResultMessage`, `CommitResultMessage`)
   and two new `oneof payload` arms (`rebase_result = 13`,
   `commit_result = 14`).

4. `src/vmm/src/main.rs` host-side stubs for the three new
   call-table function pointers exist and compile. The stubs
   for `send_rebase_result` / `send_commit_result` decode the
   protobuf result message and stash it in the same kind of
   `*RunResult` holder that resize uses. `write_input_sector`
   has a stub that errors if invoked against a slot that
   wasn't opened RW; the real wiring is fleshed out in phase
   8 when commit needs it.

5. `src/vmm/src/main.rs`'s host-side chain helpers gain a
   variant of `open_chain_devices()` (or a new helper) that
   takes a per-slot RW flag, so phase 4 (rebase host) and
   phase 8 (commit host) can opt specific slots into write
   access without duplicating the chain-opening code.

6. Unit tests in `src/shared/src/lib.rs` verify the size,
   magic, and basic field layout of each new struct. The
   pattern is the existing `#[cfg(test)] mod tests` block
   that already covers `ResizeConfig` / `ResizeResult` /
   `CreateConfig` / `CreateResult`.

7. `make instar` builds cleanly, `make lint` is clean,
   `make test-rust` passes, `pre-commit run --all-files`
   passes, and `make check-binary-sizes` is unchanged
   (no guest binary code lands in this phase).

8. The master plan
   `docs/plans/PLAN-rebase-commit.md` is updated to remove the
   "no call-table extension required" claim and document the
   `write_input_sector` addition.

Nothing in phase 1 changes user-visible behaviour. `instar
rebase` and `instar commit` still print "unrecognized
subcommand" because the `Commands` enum is not extended in
this phase — that happens in phases 4 and 8 alongside the
host CLI for each operation.

## Open questions

### 1. Magic values for the new structs

Working choices (4-byte ASCII, big-endian as displayed when
read off disk):

- `RebaseConfig::MAGIC = 0x52454241` (`"REBA"`)
- `RebaseResult::MAGIC = 0x52425253` (`"RBRS"`)
- `CommitConfig::MAGIC = 0x434F4D4D` (`"COMM"`)
- `CommitResult::MAGIC = 0x434F5253` (`"CORS"`)

None collide with the existing inventory in
`src/shared/src/lib.rs`. Confirm or pick different values
before step 1a runs.

### 2. Initial error-code sets

The error codes below are the minimum that phases 2–9 are
likely to need. They are append-only, so phases that discover
new failure modes can add codes without breaking phase 1
contracts.

`RebaseResult::ERROR_*`:

- `ERROR_OK = 0`
- `ERROR_UNSUPPORTED_FORMAT = 1` — overlay is not qcow2 or
  vmdk monolithicSparse
- `ERROR_NEW_BACKING_INCOMPATIBLE = 2` — new backing's
  virtual size < overlay's, or new backing's format is not
  one we accept
- `ERROR_EXTERNAL_DATA_FILE = 3` — qcow2 with the external-
  data-file incompatible feature; refuse to match qemu-img
- `ERROR_LUKS_UNSUPPORTED = 4` — overlay or new backing is
  LUKS-wrapped; v1 refuses
- `ERROR_CHAIN_DEPTH = 5` — old or new chain exceeds
  `MAX_CHAIN_DEVICES` (16, in `src/shared/src/lib.rs`)
- `ERROR_HEADER_MISMATCH = 6` — overlay's header changed
  during the operation (defensive read-back check)

`CommitResult::ERROR_*`:

- `ERROR_OK = 0`
- `ERROR_UNSUPPORTED_FORMAT = 1`
- `ERROR_NO_BACKING = 2` — overlay has no backing reference
- `ERROR_EXTERNAL_DATA_FILE = 3`
- `ERROR_LUKS_UNSUPPORTED = 4`
- `ERROR_BACKING_TOO_SMALL = 5` — backing's virtual size is
  smaller than the highest cluster the overlay has
  allocated
- `ERROR_OVERLAY_LARGER_THAN_BACKING = 6` — overlay's virtual
  size exceeds backing's; commit refuses
- `ERROR_HEADER_MISMATCH = 7`

Confirm the lists; either can be trimmed if a working default
proves unnecessary, or extended later by appending new codes.

### 3. Field layout of `RebaseConfig`

Working draft:

```rust
#[repr(C)]
#[derive(Clone, Copy)]
pub struct RebaseConfig {
    pub magic: u32,                  // 0x52454241 "REBA"
    pub overlay_format: u32,         // ImageFormat as u32
    pub new_backing_format: u32,     // 0 = auto, else ImageFormat as u32
    pub flags: u32,                  // FLAG_UNSAFE, FLAG_QUIET, FLAG_DETACH

    pub sector_size: u32,
    pub overlay_cluster_size: u32,   // 0 if not qcow2
    pub overlay_virtual_size: u64,

    // Device-slot layout in the input chain (see ChainConfig).
    // The host attaches old chain images at slots
    // [old_chain_first, old_chain_first + old_chain_count),
    // then new chain images at
    // [new_chain_first, new_chain_first + new_chain_count).
    pub old_chain_first: u32,
    pub old_chain_count: u32,
    pub new_chain_first: u32,
    pub new_chain_count: u32,

    // Backing-file path string written into the overlay's
    // header by the guest. 1024 bytes is the same cap as
    // CreateConfig::backing_file (see src/shared/src/lib.rs).
    pub new_backing_path: [u8; 1024],
    pub new_backing_path_len: u32,

    pub _reserved: [u8; 60],
}
```

Flags:

- `FLAG_UNSAFE = 1 << 0` — `-u` metadata-only mode; guest
  skips chain comparison and just rewrites the backing
  pointer
- `FLAG_QUIET = 1 << 1`
- `FLAG_DETACH = 1 << 2` — `new_backing_path_len == 0`; the
  overlay becomes standalone

Open subquestion: is 1024 bytes enough for the new backing
path? `qemu-img` enforces a 1023-byte cap on backing-file
strings in qcow2 headers (the length field is a u32 but the
header extension shape limits practical usage). Working
answer: yes, 1024 with `new_backing_path_len < 1024` matches
qemu's effective cap and matches `CreateConfig`. Revisit if
phase 2 finds an edge case.

### 4. Field layout of `CommitConfig`

Working draft:

```rust
#[repr(C)]
#[derive(Clone, Copy)]
pub struct CommitConfig {
    pub magic: u32,                  // 0x434F4D4D "COMM"
    pub overlay_format: u32,
    pub backing_format: u32,
    pub flags: u32,                  // FLAG_QUIET

    pub sector_size: u32,
    pub overlay_cluster_size: u32,   // 0 if not qcow2
    pub backing_cluster_size: u32,   // 0 if not qcow2

    pub overlay_virtual_size: u64,
    pub backing_virtual_size: u64,

    // Input slot 0 is the overlay (opened RW by the host).
    // Slots 1..N are the backing's own ancestor chain, in
    // order. The backing itself is the output device. The
    // guest uses write_input_sector(0, ...) for the overlay
    // metadata clear pass.
    pub backing_chain_first: u32,    // typically 1
    pub backing_chain_count: u32,    // 0 if backing has no parents of its own

    pub _reserved: [u8; 60],
}
```

Flags:

- `FLAG_QUIET = 1 << 0`

(No `FLAG_UNSAFE` — commit has no metadata-only mode in
qemu-img.)

### 5. Field layout of `RebaseResult`

Working draft:

```rust
#[repr(C)]
#[derive(Clone, Copy)]
pub struct RebaseResult {
    pub magic: u32,                  // 0x52425253 "RBRS"
    pub overlay_format: u32,
    pub mode: u32,                   // MODE_SAFE / MODE_UNSAFE
    pub error: u32,

    pub clusters_copied: u64,        // safe mode only
    pub bytes_copied: u64,           // safe mode only

    pub _reserved: [u8; 56],
}
```

Mode constants:

- `MODE_UNSAFE = 0`
- `MODE_SAFE = 1`

### 6. Field layout of `CommitResult`

Working draft:

```rust
#[repr(C)]
#[derive(Clone, Copy)]
pub struct CommitResult {
    pub magic: u32,                  // 0x434F5253 "CORS"
    pub overlay_format: u32,
    pub backing_format: u32,
    pub error: u32,

    pub clusters_committed: u64,
    pub bytes_committed: u64,
    pub overlay_clusters_cleared: u64,

    pub _reserved: [u8; 56],
}
```

### 7. Should `write_input_sector` enforce per-slot RW at the call-table boundary?

Working answer: **yes, at the host-side stub**. The host's
implementation checks the requested slot's open-mode flag
(set when the slot was attached) and returns `false` if the
slot is not RW. The guest treats `false` the same way it
treats `false` from `write_output_sector`: it aborts the
operation with `ERROR_HEADER_MISMATCH` (or a new
`ERROR_INTERNAL` code if a more specific failure mode is
warranted; appendable later).

This keeps the security property that the guest cannot
escalate write access by addressing the wrong slot.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | Add four new structs to `src/shared/src/lib.rs` immediately after the existing `ResizeResult` block (around current line 2560). Copy `ResizeConfig` (lines 2394–2463) and `ResizeResult` (lines 2495–2559) as templates. Define `RebaseConfig`, `RebaseResult`, `CommitConfig`, `CommitResult` with the exact field layouts in open questions 3–6. Add the magic constants and error-code constants exactly as listed in open questions 1 and 2 above. Add an `is_valid(&self) -> bool` method on each that checks `self.magic == Self::MAGIC`. Do **not** add address constants for them — they reuse `OPERATION_CONFIG_ADDR = 0x00081000` (the existing per-op config slot). Run `cargo build -p shared` and `cargo test -p shared` after writing; iterate until clean. |
| 1b | medium | sonnet | none | Append unit tests for the four new structs to the existing `#[cfg(test)] mod tests` block in `src/shared/src/lib.rs`. Mirror the existing `resize_config_*` and `resize_result_*` tests: assert `MAGIC` value, assert `is_valid()` returns true with magic set and false with magic zeroed, assert `size_of::<T>()` matches expectation (calculate from field list), and assert `#[repr(C)]` alignment is 8. The tests should not require any other module changes. |
| 1c | medium | opus | none | Extend `CallTable` in `src/shared/src/lib.rs` (struct ends at line 737). Append three new function-pointer fields at the very end, after `send_resize_result` and `read_output_sector`. Field order: `send_rebase_result: unsafe extern "C" fn(*const RebaseResult)`, `send_commit_result: unsafe extern "C" fn(*const CommitResult)`, `write_input_sector: unsafe extern "C" fn(u32, u64, *const u8, usize) -> bool`. The first parameter of `write_input_sector` is the input device index; the rest match `write_output_sector`'s shape (sector, buf, len). Bump `CallTable::VERSION` from 14 to 15 (line 1057). Add doc comments explaining each new pointer (use the existing `read_output_sector` doc block at lines 722–728 as the model). |
| 1d | medium | sonnet | none | Extend `crates/guest-protocol/proto/guest.proto`. Add two new message types `RebaseResultMessage` and `CommitResultMessage` mirroring `ResizeResultMessage` (lines 236–254). Field list for `RebaseResultMessage`: `string overlay_format = 1`, `string mode = 2` (`"safe"` or `"unsafe"`), `uint64 clusters_copied = 3`, `uint64 bytes_copied = 4`, `uint32 error = 5`. Field list for `CommitResultMessage`: `string overlay_format = 1`, `string backing_format = 2`, `uint64 clusters_committed = 3`, `uint64 bytes_committed = 4`, `uint64 overlay_clusters_cleared = 5`, `uint32 error = 6`. Append two new arms to the `GuestMessage` oneof (currently ending at line 271): `RebaseResultMessage rebase_result = 13;` and `CommitResultMessage commit_result = 14;`. Run `cargo build -p guest-protocol` and verify the generated Rust compiles. |
| 1e | high | opus | none | Wire host-side stubs for the three new `CallTable` pointers in `src/vmm/src/main.rs`. There is no single "CallTable stub" file — the function-pointer slots are populated where the call table is installed in guest memory (search for the existing `send_resize_result` wiring, likely around the `populate_call_table` or `install_call_table` helper that resize phase 7 added). Add: a `send_rebase_result` callback that decodes the protobuf `RebaseResultMessage` from the serial decoder and updates a `RebaseRunResult` holder (mirror the resize equivalent); a `send_commit_result` callback that does the same for commit; a `write_input_sector` callback that looks up the requested slot in the device set, returns `false` if the slot was not opened RW, and otherwise dispatches the write through the same backing-store path that `write_output_sector` uses. Add a `RebaseRunResult` and `CommitRunResult` struct in `main.rs` matching `ResizeRunResult` (same field layout as the corresponding `*Result` struct in `shared`, minus the magic). The stubs do not need to be reachable from any subcommand yet — only the build must pass. Note: this step crosses host/guest semantics and the call-table boundary; use opus. |
| 1f | high | opus | none | Add `open_chain_devices_rw` to `src/vmm/src/main.rs` (or extend the existing `open_chain_devices` at lines 2165–2228 with an extra parameter). Signature: same as `open_chain_devices` plus a `rw_slots: &[usize]` parameter listing the device slot indices (relative to `start_idx`) that should be opened with `BackingStore::open_rw_existing()` instead of `BackingStore::open()`. The function records a per-slot "is_writable" flag on the device set so the `write_input_sector` stub from step 1e can enforce it. Do not change the existing `open_chain_devices` signature in a breaking way — either add a new variant or add a default-`&[]` parameter and update existing call sites. Add a small unit test (or a doc comment with an example) that documents the intended usage from phase 8. Use opus: the change touches the device-attachment boundary and needs careful read of `DeviceSet` and `VirtioBlockDevice`. |
| 1g | low | sonnet | none | Update `docs/plans/PLAN-rebase-commit.md`. In the "Call-table extension" section, remove the "**None required.**" claim and replace it with a paragraph that lists the three new pointers (`send_rebase_result`, `send_commit_result`, `write_input_sector`) and explains why `write_input_sector` is needed (commit's overlay-clear pass writes the overlay metadata while the overlay is attached as an input device). In the device-attachment table for commit, note that `Input 0 (Overlay)` is opened RW via `open_chain_devices_rw` and that the guest uses `write_input_sector(0, ...)` to clear the overlay's L2 / refcount entries. Append a note to the Execution table row for phase 1 pointing at this phase plan. |
| 1h | low | sonnet | none | Run `pre-commit run --all-files` from the worktree root. Resolve any rustfmt / clippy findings. Run `make instar` and `make test-rust`. Verify `make check-binary-sizes` is unchanged (the guest binaries should not need to recompile against the new pointers because no operation calls them yet — but if `core.bin` references `CallTable` size anywhere, it may need a rebuild; check). Stage and present a single commit covering all of steps 1a–1g with the commit-message convention from CLAUDE.md (50-char first line, 75-char body wrap, Prompt paragraph, Signed-off-by, Co-Authored-By with model + context window + effort level). |

## Agent guidance

### Execution model

All implementation work for this phase is done by sub-agents,
never in the management session. The management session
(this conversation) is reserved for review and decision-
making. After each step the management session:

1. Reads the actual files that were supposed to change.
2. Confirms no unrelated files were modified.
3. Runs the lint / test commands that the step's brief
   names.
4. Either commits, asks for a retry with a sharper brief, or
   upgrades the model.

### Model and effort notes

- Steps 1a, 1b, 1d, 1g, 1h are mechanical extensions of
  well-established patterns. Sonnet at medium / low effort
  is enough provided the briefs name exact line ranges and
  templates to copy.
- Steps 1c, 1e, 1f cross the host/guest boundary or touch
  call-table semantics. Use opus. The opus context window
  also helps because steps 1e and 1f have to hold the
  device-attachment code and the call-table installation code
  in context simultaneously.

### Management session review checklist

After each step:

- [ ] Read the changed files — don't trust the agent's
      summary.
- [ ] No unrelated files modified.
- [ ] `cargo build -p shared` (step 1a, 1b, 1c).
- [ ] `cargo test -p shared` (step 1a, 1b).
- [ ] `cargo build -p guest-protocol` (step 1d).
- [ ] `cargo build --workspace` (step 1e, 1f, 1h).
- [ ] `cargo clippy --workspace -- -D warnings` (step 1h).
- [ ] `make check-binary-sizes` (step 1h).
- [ ] `pre-commit run --all-files` (step 1h).
- [ ] The new structs and pointers match the field
      layouts in open questions 3–6.
- [ ] No existing CallTable function pointer or struct field
      moved — all changes are append-only.

## Administration and logistics

### Success criteria

Phase 1 is complete when:

* All eight steps above are merged in one commit on the
  `rebase-commit` branch.
* `make instar` builds and `make lint` is clean.
* `make test-rust` passes, including the new struct-layout
  unit tests from step 1b.
* `make check-binary-sizes` is unchanged.
* `pre-commit run --all-files` is clean.
* `docs/plans/PLAN-rebase-commit.md` reflects the
  call-table addition (no more "None required" claim).
* The four new structs and three new call-table pointers
  compile cleanly with no `dead_code` warnings (the build
  treats them as `pub`, which suppresses the warning for
  in-crate-only-unused items).

### Future work created by this phase

None directly. Subsequent phases consume the ABI:

- Phase 2 (rebase planners) defines pure functions that
  produce patch lists from parsed headers; they take
  `RebaseConfig`-shaped inputs but do not depend on the
  struct directly.
- Phase 3 (rebase guest binary) reads `RebaseConfig` from
  `OPERATION_CONFIG_ADDR` and calls `send_rebase_result`.
- Phase 4 (rebase host) populates `RebaseConfig`, opens the
  chain devices via `open_chain_devices_rw`, and consumes
  the host-side `RebaseRunResult` produced by the
  `send_rebase_result` stub.
- Phases 6, 7, 8 do the equivalent for commit.

If phase 2 or 6 discovers it needs additional fields in the
config struct, append them inside the `_reserved` tail and
shrink the reserved padding accordingly — no other phase has
to change because the layout is `#[repr(C)]` and the
non-reserved fields keep their offsets.

### Documentation index maintenance

This is a phase plan, not a master plan. It is **not** added
to `docs/plans/order.yml` per the convention. The master plan
will link to it from its Execution table (handled in step 1g).

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the
work you intend to do aligns with that plan.
