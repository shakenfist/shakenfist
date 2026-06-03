# PLAN-rebase-commit phase 04: rebase host CLI

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read the
resize host CLI in `src/vmm/src/main.rs` (its `ResizeArgs`,
`run_resize_nonraw`, `run_resize_guest`, `render_resize_success`,
`map_resize_error` family), the backing-chain discovery code
(`discover_backing_chain`, `BackingChain`, `ChainImage`), the
chain-device attachment helpers (`open_chain_devices`,
`open_chain_devices_rw`), the call-table installation paths
(`src/vmm/src/main.rs` around the guest-memory writes for
ResizeConfig), and the create host CLI's `-b` / detach handling.
Ground answers in what the code actually does today.

Phase plans live alongside the master plan in `docs/plans/`,
named `PLAN-rebase-commit-phase-NN-<descriptive>.md`. The
master plan is [PLAN-rebase-commit.md](/components/instar/plans/PLAN-rebase-commit/).
This phase is the fourth of twelve.

I prefer one commit per logical step.

## Situation

Phases 1–3 (partial) shipped:

- Phase 1: shared ABI — `RebaseConfig`, `RebaseResult`,
  `send_rebase_result`, `write_input_sector` in `CallTable`,
  `RebaseResultMessage` and `CommitResultMessage` in the
  protobuf, host-side stubs for the new call-table function
  pointers, and `open_chain_devices_rw`.
- Phase 2: planner crate `src/crates/rebase/` covers qcow2
  unsafe + safe modes (in-place rewrite only,
  `refcount_bits == 16` only) and vmdk monolithicSparse
  unsafe mode.
- Phase 3 (partial): guest binary `src/operations/rebase/`
  with `_start`, format dispatch, `apply_rebase_plan`
  helper, `run_qcow2_unsafe`, and `run_vmdk_unsafe`. qcow2
  safe-mode runner and the chain-walking helper are
  deferred follow-ups.

Phase 4 delivers the host CLI: `instar rebase`. The user
types `instar rebase -b NEW_BACKING OVERLAY` (or
`-b "" OVERLAY` to detach, or `-u -b NEW_BACKING OVERLAY`
for unsafe metadata-only rebase). The host parses args,
discovers the old chain (the overlay's current backing
chain) and the new chain (the chain starting from
`-b NEW_BACKING`), attaches both chains as input devices
plus the overlay as the output device, populates
`RebaseConfig`, launches the rebase guest binary in KVM,
harvests `RebaseResultMessage`, and renders human or JSON
output.

The relevant existing infrastructure this phase builds on:

- **Resize host CLI as the structural template**
  (`src/vmm/src/main.rs`). Specifically:
  - `enum Commands` (around line 2445) where the new
    `Rebase(RebaseArgs)` variant goes.
  - `struct ResizeArgs` (around line 2469) as the clap
    shape for `RebaseArgs`.
  - `fn run_resize_nonraw` (around line 3374) as the
    template for `run_rebase`.
  - `fn run_resize_guest` (around line 3507) as the
    template for `run_rebase_guest` — KVM/VM setup,
    guest-memory writes, device attachment, vCPU loop,
    result harvesting.
  - `fn render_resize_success` (around line 3295) and
    `fn map_resize_error` (around line 3324) as the
    templates for the rebase rendering / error mapping.
- **Backing-chain discovery**
  (`src/vmm/src/main.rs` around line 1939
  `discover_backing_chain`). Returns a `BackingChain`
  struct of resolved + validated `ChainImage` entries.
  Phase 4 calls it twice: once to discover the old chain
  starting from the overlay, and once for the new chain
  starting from `-b`. Both calls share the same
  `SecurityConfig` allowlist.
- **Chain-device attachment**
  (`src/vmm/src/main.rs` around line 2185
  `open_chain_devices`). Reads `BackingChain` and
  attaches each image as a read-only virtio-block device
  at a sequential device slot starting from `start_idx`.
  Rebase uses this for both chains; no slot is RW
  (the overlay is the output device, not an input).
- **Overlay-as-output attachment**
  (`BackingStore::open_rw_existing` in
  `src/vmm/src/backing.rs`). The overlay being rebased
  is attached as the output device opened RW, the same
  pattern resize uses for the file being resized.
- **Phase 1 host-side `RebaseResult` plumbing**
  (`src/vmm/src/main.rs` around the existing
  `send_rebase_result` and `send_commit_result` stubs
  added in phase 1 step 1e). The vCPU loop in phase 4
  needs to match on `Payload::RebaseResult` and
  populate a host-side `RebaseRunResult` holder, the same
  way the resize loop populates `ResizeRunResult`.
- **Detach pattern**
  (`src/vmm/src/main.rs` `run_create_nonraw` around
  line 7931). Create accepts `-b ""` for "no backing"
  by checking `Option<String>::is_some()` and the inner
  string's emptiness; rebase mirrors this to set
  `FLAG_DETACH`.

## Mission and problem statement

After phase 4 lands:

1. `enum Commands` in `src/vmm/src/main.rs` carries a
   `Rebase(RebaseArgs)` variant; the clap subcommand
   dispatch routes to a new `run_rebase` function.

2. `struct RebaseArgs` accepts the same surface area as
   `qemu-img rebase`:
   - `filename: String` — positional, the overlay being
     rebased.
   - `-f, --format <FMT>` — overlay format hint
     (`Option<String>`); host passes through to the
     guest, the guest probes anyway.
   - `-b, --backing <BACKING>` — required. The new
     backing path. Empty string (`-b ""`) triggers
     detach mode.
   - `-F, --backing-format <FMT>` — optional new-backing
     format hint.
   - `-u` — unsafe / metadata-only mode (see open
     question 4 about the deferred safe-mode runner).
   - `-q, --quiet` — quiet success line.
   - `--output <human|json>` — output format, default
     human.

3. `fn run_rebase`:
   - Resolves the overlay's absolute path; validates it
     exists and is opened with `O_RDWR`.
   - Validates `-b` and resolves it to an absolute path
     when non-empty.
   - Pre-checks that the new backing file exists (when
     not detaching). Returns a clear error before the
     guest launches if it doesn't.
   - Calls `discover_backing_chain` twice: once for the
     overlay (yielding the old chain, of which device 0
     is the overlay itself — see open question 5) and
     once for the new backing (yielding the new chain).
     For detach the new chain call is skipped and
     `new_chain_count` is zero.
   - Computes `old_chain_first` / `old_chain_count` /
     `new_chain_first` / `new_chain_count` for the
     `RebaseConfig`. Slot layout per open question 5.
   - Validates the total device count against
     `MAX_CHAIN_DEVICES` (currently 16) — host-side
     pre-check so the user gets a clear error before
     launching the guest.
   - Calls `run_rebase_guest` with the populated args.

4. `fn run_rebase_guest`:
   - Loads `core.bin` and `rebase.bin` from the binary
     directory.
   - Stands up the KVM VM, guest memory, GDT, page
     tables, mirroring `run_resize_guest`.
   - Writes the populated `RebaseConfig` to
     `OPERATION_CONFIG_ADDR`. The struct layout matches
     the phase 1 definition in `src/shared/src/lib.rs`;
     phase 4 writes field-by-field at the matching byte
     offsets (same idiom resize uses).
   - Opens the overlay via `BackingStore::open_rw_existing`
     and attaches it as the output device at device slot 0.
   - Attaches the old chain via `open_chain_devices`
     starting at the slot immediately after the output.
   - Attaches the new chain via `open_chain_devices`
     starting at the slot immediately after the old chain.
   - Runs the vCPU loop. On each `IoOut` on the serial
     port, feeds bytes to the existing `serial_decoder`
     and matches on `Payload::RebaseResult` to harvest
     `mode`, `clusters_copied`, `bytes_copied`, `error`
     into a `RebaseRunResult` struct.
   - Returns the harvested result.
   - No post-pass (resize's post-pass `set_len` and
     preallocation are not needed for rebase — the
     overlay's file size doesn't change in either mode).

5. `fn render_rebase_success` and `fn map_rebase_error`:
   - `render_rebase_success` prints either
     `Image rebased.` / `Image detached.` (human mode)
     or a structured JSON envelope with `overlay`,
     `overlay_format`, `mode`, `clusters_copied`,
     `bytes_copied`, and `new_backing` (omitted when
     detaching).
   - `map_rebase_error` maps every
     `RebaseResult::ERROR_*` constant (0..=13) to a
     user-readable message. The mapping is exhaustive
     (no catch-all `_`); a future error code addition
     becomes a compile-time prompt.

6. The host CLI accepts the safe-mode default even though
   phase 3 step 3e is deferred. The guest returns
   `ERROR_UNSUPPORTED_FORMAT` for safe-mode rebase
   today; the user sees `rebase: format does not support
   rebase in safe mode (try -u)`. This is the same
   pattern create uses for unsupported subformats.

7. Tests: phase 4 adds a small set of smoke tests under
   `tests/test_rebase.py` that exercise:
   - qcow2 `-u` rebase onto a new backing (success
     path).
   - qcow2 `-u -b ""` detach.
   - vmdk `-u` rebase onto a new backing.
   - Failure paths: non-existent backing file, oversized
     backing path, missing `-b` flag.
   The deeper validation tests (round-trip against
   `qemu-img`) belong to phase 5.

8. `make instar` builds clean, `make lint` is clean,
   `pre-commit run --all-files` is clean,
   `make test-rust` passes, and `make test-integration`
   runs the new smoke tests.

## Open questions

### 1. Is `-b` required, or can rebase with no `-b` reset to a no-op?

Working choice: **required**. `qemu-img rebase` requires
`-b`; not providing it is an error. Clap enforces this
with `required = true` on the field. Detach is the
explicit empty-string form `-b ""`.

Alternative: allow `instar rebase OVERLAY` with no `-b`
to default to a re-validation pass against the current
backing. Rejected because the use case is unclear and the
current behaviour is "no-op" which a user can't easily
distinguish from "did nothing because it was a no-op".

### 2. Should the host pre-check that the new backing file exists?

Working choice: **yes**, except in `-u` mode (mirroring
qemu-img's `-u` semantics). Unsafe mode is the "trust me,
the path is right" knob; checking the file's existence in
unsafe mode defeats the point. In safe mode the host
verifies the file exists before launching the guest so
the user gets a clear error message.

### 3. Should the host pre-check that the overlay format is qcow2 or vmdk?

Working choice: **no**. Match resize's pattern — the
guest reports `ERROR_UNSUPPORTED_FORMAT` for any other
format and the host renders a clear message. Adding a
host-side pre-check duplicates the format-detection logic
the guest already has to do.

### 4. Safe-mode CLI surface with the deferred phase 3 step 3e

Working choice: **accept safe mode in the CLI, let the
guest return `ERROR_UNSUPPORTED_FORMAT`** until phase 3
step 3e lands. The user sees an error that points at the
`-u` workaround. Phase 3 step 3e flips this without any
host-side change.

Alternative: have the host refuse safe mode and require
`-u` until 3e ships. Rejected because the host shouldn't
know about the guest's per-subcommand support matrix —
that asymmetry would be hard to keep in sync.

### 5. Device slot layout

The phase 3 plan committed to:

- Slot 0: the overlay being rebased, attached as the
  *output* device opened RW.
- Slots [old_chain_first..) : the old backing chain
  (read-only inputs), starting from the overlay's
  immediate parent.
- Slots [new_chain_first..) : the new backing chain
  (read-only inputs).

Working choice: **old_chain_first = 0, new_chain_first =
old_chain_count**. The overlay is the output, separate
from the input numbering, so input slot 0 is the old
chain's immediate parent. This is the simplest
contract.

Subquestion: should the old chain include the overlay
itself? No — the overlay is the output device. The old
chain only carries the parent and ancestors.
`discover_backing_chain` walks the overlay's parents; the
helper might or might not include the overlay itself in
the returned `BackingChain` depending on its current
implementation. Phase 4 reads the existing implementation
and strips the overlay from the front of the chain if it
was included.

### 6. Detach + chain discovery

When the user passes `-b ""`, the new backing chain is
empty. Phase 4 skips the second `discover_backing_chain`
call and sets `new_chain_count = 0`. The guest sees
`FLAG_DETACH` and decides what to do (the planner
writes a zero backing-file pointer). The new backing
path bytes in the config are empty.

### 7. Path resolution for `-b`

When the user types `-b backing.qcow2` (relative path),
how does the host resolve it? Two reasonable
interpretations:

- Relative to the user's current working directory (like
  most CLI tools).
- Relative to the overlay's directory (like qemu-img,
  which stores backing paths relative to the overlay
  file).

Working choice: **match qemu-img** — when the path is
relative, resolve it relative to the overlay's parent
directory before recording it. The bytes embedded in the
overlay's header are the user-typed string verbatim
(unchanged), so qemu-img reads the same backing later
the same way. The host's existence-check uses the
resolved absolute path.

### 8. Output rendering for safe mode that copied zero clusters

In safe mode, if the comparison loop finds the old and
new chains identical at every guest cluster, no copies
happen. The output should still say "Image rebased"
because the metadata pointer was rewritten; the JSON
envelope reports `clusters_copied: 0`. Confirm this is
acceptable.

### 9. Exit code on guest error

Working choice: **non-zero on any guest error**. The
host returns `Err(...)` from `run_rebase`; `main` exits
with non-zero. Same as resize.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | **Shipped** as `913ce15`. RebaseArgs struct, Commands::Rebase variant, run_rebase dispatch stub, RebaseRunResult holder. |
| 4b | medium | sonnet | none | **Shipped** as `3a39c33`. render_rebase_success (quiet/json/human modes; "Image rebased." / "Image detached." messages) and map_rebase_error (exhaustive over 14 RebaseResult::ERROR_* constants 0..=13). |
| 4c | high | opus | none | **Shipped** as `dc39783`. Implements run_rebase up to the guest-launch boundary: probe_rebase_target (parses qcow2/vmdk header for format + virtual_size + cluster_size), overlay-exists check, new-backing path resolution against overlay parent dir, backing-exists pre-check in safe mode (skipped in -u), oversized-path rejection, discover_backing_chain twice (old chain stripped of the overlay; new chain only if not detaching), MAX_CHAIN_DEVICES combined-depth check. Errors out at the guest-launch boundary pointing at step 4d. |
| 4d | high | opus | none | **Shipped.** run_rebase_guest in src/vmm/src/main.rs mirrors run_resize_guest for KVM/VM setup. Writes RebaseConfig at OPERATION_CONFIG_ADDR with the [u8; 1024] path embedded inline at offset 48. Device layout: old chain parents fill input slots [0..old_count), new chain fills [old_count..old_count+new_count), overlay attached as the output at the next slot via BackingStore::open_rw_existing; a 1-sector stub fills input slot 0 when both chains are empty (matches resize). Matches Payload::RebaseResult in the vCPU loop and populates RebaseRunResult. No post-pass -- the overlay's file size doesn't change. End-to-end smoke verified: qcow2 -u rebase to same-length path overwrites backing-filename; qcow2 -u -b "" clears it. |
| 4e | medium | sonnet | none | **Partial.** Shipped alongside 4d: tests/test_rebase.py TestRebaseSuccessPaths now runs `test_qcow2_unsafe_rebase_records_new_backing` and `test_qcow2_unsafe_detach` end-to-end. vmdk monolithicSparse test and qemu-img round-trip remain skipped pending phase 5 step 5f. |
| 4f | low | sonnet | none | **Partial.** Pre-commit clean, master plan updated to reflect partial completion with shipping commit hashes. |

## Agent guidance

### Execution model

Same as phases 1–3: implementation runs in the management
session unless explicitly delegated. Use opus for 4c and
4d because they hold the chain-discovery code, the
device-attachment helpers, the RebaseConfig layout, and
the vCPU loop in mind simultaneously.

### Planning effort

The master plan flagged phase 4 as **medium** overall.
Within the phase, steps 4c and 4d are high-effort; 4a,
4b, 4e, 4f are medium-low.

### Step ordering

Strict dependency: 4a → 4b → 4c → 4d → 4e → 4f. The
runner functions in 4c need the args struct from 4a and
the rendering helpers from 4b. 4d is consumed by 4c (its
caller). 4e exercises the full stack.

### Management session review checklist

- [ ] Files that were supposed to change actually
      changed.
- [ ] No unrelated files modified.
- [ ] `make instar` builds, `make lint` clean.
- [ ] `make check-binary-sizes` unchanged (no
      regressions on existing operation binaries).
- [ ] `make test-rust` passes.
- [ ] `make test-integration tests/test_rebase.py`
      passes.
- [ ] Pre-commit clean.
- [ ] The error code mapping in `map_rebase_error` has
      no catch-all `_` arm.
- [ ] The device-slot ordering in `run_rebase_guest`
      matches the contract documented in open question
      5 (overlay = slot 0 output, old chain at slot
      1.., new chain at slot 1 + old_chain_count..).
- [ ] No new `unsafe` outside the existing guest-
      memory and device-attachment patterns.

## Administration and logistics

### Success criteria

Phase 4 is complete when:

* `instar rebase --help` shows the documented surface.
* `instar rebase -u -b NEW OVERLAY` works for qcow2 and
  vmdk overlays (subject to phase 2 / 3 caveats).
* `instar rebase -u -b "" OVERLAY` detaches.
* `instar rebase -b NEW OVERLAY` returns
  `ERROR_UNSUPPORTED_FORMAT` (safe-mode runner deferred);
  the host renders a message pointing at `-u`.
* Smoke tests in `tests/test_rebase.py` pass.
* `pre-commit`, `make instar`, `make lint`,
  `make test-rust`, `make test-integration` all pass.
* The execution-table row for phase 4 in
  `PLAN-rebase-commit.md` is marked complete with
  shipping commit hashes.

### Future work created by this phase

- Safe-mode rebase becomes user-visible once phase 3
  step 3e lands. No host-side change needed; the guest
  starts returning `ERROR_OK` for the safe-mode path.
- vmdk safe-mode rebase becomes user-visible once
  phase 2 step 2e + phase 3 vmdk-safe-mode runner land.
- Round-trip cross-validation against `qemu-img rebase`
  is phase 5's responsibility.
- The "did nothing because everything matched" case
  (open question 8) might warrant a distinct exit
  signal in the future — currently it's identical to
  the "rebased one or more clusters" case.

### Bugs fixed during this work

To be filled in as work progresses.

### Documentation index maintenance

Not added to `docs/plans/order.yml` — phase plans live
alongside the master plan but only the master plan is
indexed.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
