# PLAN-resize phase 9: preallocation modes

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (POSIX `posix_fallocate`, Linux
`fallocate(FALLOC_FL_ZERO_RANGE)`, qemu-img preallocation
semantics), research as needed to give a confident answer. Flag
any uncertainty explicitly rather than guessing.

This is a phase plan under `PLAN-resize.md`. Refer to that
master plan for overall context. Phases 1–8 shipped the planner,
the guest binary, and the host CLI; phase 9 lifts the
deferred-from-phase-8 `--preallocation=falloc|full` post-pass.

## Mission

Replace the two `phase-9-pointer` errors in `run_resize_raw` and
`run_resize_nonraw` with the actual host-side preallocation
finalisation:

1. **`off`**: no post-pass (the default; already handled).
2. **`falloc`**: `posix_fallocate` over the newly-added file
   region. Reserves disk blocks without writing data.
3. **`full`**: zero the newly-added file region. Tries
   `fallocate(FALLOC_FL_ZERO_RANGE)` first (fast — no actual
   writes on btrfs / ext4 / xfs); falls back to a 64 KiB
   `pwrite` loop on filesystems that don't support it (tmpfs,
   NFS, some FUSE).
4. **`metadata`**: the qcow2/vhdx grow planners are responsible
   for this mode (master plan, Per-format resize plans). Phase
   9 leaves the planner's existing `PreallocationUnsupported`
   rejection in place; lifting it is queued under Future work.

The "newly-added file region" is interpreted format-by-format:

- **raw + grow**: `[current_virtual_size, new_virtual_size)`.
  Matches qemu exactly.
- **raw + shrink**: no post-pass. Falloc/full + shrink is
  rejected up-front as nonsensical (you can't preallocate
  space you're discarding).
- **non-raw + grow**: `[current_file_size, new_file_size)` —
  i.e. the bytes the planner physically appended past the
  pre-resize EOF (new L1 region for qcow2 L1Grow, new BAT
  region for vhd / vhdx / vmdk relocate, etc.). This is a
  **deliberate divergence from qemu**, which preallocates the
  entire data-region span for the new virtual size. Lifting
  this to full qemu parity requires per-format walk-and-
  populate logic comparable to a `dd if=/dev/zero` over the
  data region; queued under Future work.
- **non-raw + shrink**: no post-pass. Same rationale as raw.

The phase plan's master-plan note ("the host knows
`total_file_size` from `ResizeResult` and can `fallocate` the
rest") establishes this interpretation — the host preallocates
the *file's* new region, not the format's *data* region.
Phase 13's `docs/quirks.md` documents the divergence.

## What the survey turned up

- **`apply_preallocation`** at
  `src/vmm/src/main.rs:8511-8549` already does the heavy
  lifting: takes `(file, mode, offset, len)`, dispatches on
  `"falloc"` / `"full"`, calls `posix_fallocate` or
  `fill_zeros`. Reusable as-is; only the error-message prefix
  (`"create: posix_fallocate failed"`) is operation-specific.
- **`fill_zeros`** at `src/vmm/src/main.rs:8432-8499` does
  the `fallocate(FALLOC_FL_ZERO_RANGE)` + write-loop
  fallback. Already operation-agnostic.
- **The current phase-8 raw rejection** lives in
  `run_resize_raw` (at the top of its body): rejects
  `metadata` / `falloc` / `full` with a "deferred to phase 9"
  message.
- **The current phase-8 non-raw rejection** lives in
  `run_resize_nonraw` (at the top of its body): rejects
  `falloc` / `full` with a "deferred to phase 9" message.
  `metadata` is routed through to the guest (which the
  planner currently rejects with
  `PREALLOCATION_UNSUPPORTED`).
- **`run_create_nonraw`** at `src/vmm/src/main.rs:7191-7231`
  is the reference call site for `apply_preallocation` (used
  for qcow2's data-region post-pass).

## Algorithmic design

### Shared helper: make `apply_preallocation` op-agnostic

The existing error messages hard-code `"create:"`. Phase 9
parameterises this so the resize call sites surface
`"resize:"` instead:

```rust
fn apply_preallocation(
    file: &std::fs::File,
    op_name: &str,                // NEW: "create" or "resize"
    mode: &str,
    data_offset: u64,
    data_len: u64,
) -> Result<(), Box<dyn std::error::Error>>;
```

`fill_zeros` stays op-agnostic (its caller wraps the error
with the op prefix already; resize follows the same pattern).
The two existing `apply_preallocation` call sites in
`run_create_nonraw` are updated to pass `"create"`.

### Raw post-pass

```rust
fn run_resize_raw(...) -> Result<(), ...> {
    // ... validation as today ...

    // Reject shrink + falloc/full combinations up-front; they
    // are nonsensical (preallocating space we're discarding).
    let mode = args.preallocation.as_str();
    if matches!(mode, "falloc" | "full") && new_virtual_size < probed.current_virtual_size {
        return Err(format!(
            "resize: --preallocation={mode} is meaningless when shrinking"
        ).into());
    }
    if mode == "metadata" {
        return Err("resize: --preallocation=metadata is not supported for raw".into());
    }

    let file = OpenOptions::new().read(true).write(true).open(&args.filename)?;
    file.set_len(new_virtual_size)?;

    // Phase 9: post-pass the newly-added region only.  For
    // shrink (rejected above for falloc/full) and noop (size
    // unchanged) this is a no-op.
    if new_virtual_size > probed.current_virtual_size {
        let added = new_virtual_size - probed.current_virtual_size;
        apply_preallocation(&file, "resize", mode, probed.current_virtual_size, added)?;
    }
    file.sync_all()?;
    // ... render success as today ...
}
```

### Non-raw post-pass

```rust
fn run_resize_nonraw(...) -> Result<(), ...> {
    // ... existing validation up to and including the
    //     run_resize_guest call ...

    // Phase 9: post-pass the newly-appended file region.
    // This is BEFORE set_len so we operate on the physical
    // appended bytes (set_len trims/extends to the exact EOF
    // the planner reported).
    let mode = args.preallocation.as_str();
    if matches!(mode, "falloc" | "full") && result.action == RESIZE_RESULT_ACTION_SHRINK {
        return Err(format!(
            "resize: --preallocation={mode} is meaningless when shrinking"
        ).into());
    }

    let file = OpenOptions::new().write(true).read(true).open(&args.filename)?;
    if matches!(mode, "falloc" | "full")
        && result.file_size_after > result.file_size_before
    {
        let added = result.file_size_after - result.file_size_before;
        apply_preallocation(&file, "resize", mode, result.file_size_before, added)?;
    }
    file.set_len(result.file_size_after)?;
    file.sync_all()?;
    // ... render success as today ...
}
```

Notably the file is opened read+write here (instead of
write-only as in 8b) so `apply_preallocation` can call
`posix_fallocate` / `fallocate(FALLOC_FL_ZERO_RANGE)` —
both syscalls require the fd to be writable but the existing
`fill_zeros` doesn't care which mode it was opened in.

### The metadata-mode story

`metadata` is the qcow2/vhdx-specific mode where the new L2 /
BAT entries are pre-populated to point at zero clusters.
Phase 2c's `PreallocationUnsupported` rejection in the qcow2
planner stays in place; phase 5b's VHDX planner does the
same. Phase 9 doesn't lift either rejection — it stays
queued under master-plan Future work ("QCOW2 grow with
Preallocation::Metadata").

For phase 9, when the user passes `--preallocation=metadata`:

- **raw**: rejected with `"--preallocation=metadata is not
  supported for raw"` (no metadata to populate).
- **non-raw**: routed through to the guest; the planner
  returns `PreallocationUnsupported`; the host maps this to
  `"preallocation mode not supported by this format"` via
  the existing `map_resize_error`. Matches the post-phase-2c
  behaviour.

### Shrink + preallocation rejection

`falloc` / `full` only make sense when growing. Phase 9
rejects them up-front for shrink:

```text
resize: --preallocation=falloc is meaningless when shrinking
```

(qemu's behaviour: it accepts the combination silently and
performs the shrink without preallocating; matching qemu
would mean silently ignoring the flag. We reject explicitly
for clarity. Documented in `docs/quirks.md` as a deliberate
divergence.)

### Sparse-format data-region preallocation: deferred

As noted in the mission, phase 9 does not match qemu's
behaviour of preallocating the entire data-region span for
sparse formats. The Future-work entry will land per-format
data-region preallocation passes (qcow2: walk every L1 entry,
allocate L2 tables and data clusters; vhdx: write every BAT
entry's data block; vmdk: walk every GD entry; vhd: walk
every BAT entry). Each format's pass is comparable in
complexity to a half-create operation.

## Test surface

Unit tests live alongside the existing `parse_*` tests in
`resize_size_parser_tests`:

- `apply_preallocation` no-ops for `mode="off"` and `len=0`.
- `apply_preallocation` falloc-mode allocates the requested
  range (verified via `stat -c %b` block count).
- `apply_preallocation` full-mode zeros the range (verified
  by reading back and asserting all-zero).
- `apply_preallocation` full-mode's write-loop fallback
  triggers when `FALLOC_FL_ZERO_RANGE` returns `EOPNOTSUPP`
  (mocked via a test wrapper that intercepts `fallocate`).

The fallback-path test is the trickiest. The current code
calls `libc::fallocate` directly, with no abstraction layer
to mock. Options:

1. Add a `fill_zeros_with_fallback` variant that takes a
   closure for the fallocate call, so tests can inject a
   no-op closure that returns `EOPNOTSUPP`.
2. Skip the fallback test in unit tests; rely on the
   integration suite + a feature flag that disables the
   fallocate fast path during testing.
3. Test on a filesystem that doesn't support
   `FALLOC_FL_ZERO_RANGE` (e.g. tmpfs in some configurations).

*Recommendation*: option 1. Add a `fill_zeros_inner` that
takes a `fallocate_fn` closure; production callers pass the
real syscall, tests pass a closure that returns `EOPNOTSUPP`.
`fill_zeros` becomes a thin wrapper.

End-to-end integration tests (raw + qcow2 + vhd + vhdx + vmdk
× off / falloc / full / metadata) land in phase 11.

## Public API delta

None outside `src/vmm/src/main.rs`. The `apply_preallocation`
signature changes (adds `op_name`) but it's a private
function within the VMM binary — no external callers.

## Open questions

1. **Should non-raw post-pass match qemu's data-region
   semantics?** v1 doesn't. Documented divergence. *Recommendation*:
   add the per-format data-region walk in a follow-up phase
   once user demand is clear; for now the host-side post-pass
   is "preallocate the appended region of the file", which is
   a tiny fraction of what qemu does. Queue under master-plan
   Future work.

2. **Should `--preallocation=metadata` for non-raw + grow
   silently succeed when the planner rejects?** No — the
   user explicitly asked for a mode the planner doesn't
   support; surfacing the rejection is the right behaviour.

3. **`falloc` + shrink rejection wording**. qemu silently
   accepts; we reject. *Recommendation*: reject with a clear
   message ("preallocation is meaningless when shrinking")
   so the user notices they've passed conflicting flags.
   Document the divergence.

4. **Order of `apply_preallocation` vs `set_len` for
   non-raw**. `apply_preallocation` first (operates on
   `result.file_size_after` bytes the planner already wrote),
   then `set_len` to commit the exact EOF. The other order
   would have `set_len` extend the file with sparse zeros,
   then `apply_preallocation` would materialise them — also
   correct, but adds latency between the guest's writes and
   the post-pass. *Recommendation*: post-pass first, then
   `set_len`. The planner's `file_size_after` is already the
   target EOF; the post-pass just guarantees the bytes are
   physically allocated.

5. **`fsync` ordering**. `sync_all` after both post-pass and
   `set_len` so the kernel commits everything atomically.
   Matches the existing `run_create_raw`'s flow.

6. **`apply_preallocation` error mapping**. Currently
   `apply_preallocation` returns `Box<dyn Error>`. The
   resize call sites can propagate directly. The host's
   stderr message is whatever the helper produced; we don't
   wrap.

7. **`metadata` mode for raw**. qemu rejects this combination
   with `"qemu-img: Preallocation can only be set for image
   creation"` — wait, that's create-specific. For resize,
   qemu accepts `--preallocation=metadata` on raw but it's a
   no-op (raw has no metadata). *Recommendation*: match
   qemu's accept-but-noop behaviour for resize + raw +
   metadata? Or reject explicitly? Phase 9 takes the
   explicit-reject path for clarity.

8. **The `fill_zeros_inner` injection**. If we don't want to
   add the closure-based injection just for tests, an
   alternative is a conditional-compilation flag
   (`#[cfg(test)]`) that swaps the implementation. Less
   ergonomic but smaller change. *Recommendation*: closure
   injection — it doesn't add runtime overhead (the closure
   is a function pointer) and the test ergonomics are much
   better.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 9a | medium | sonnet | none | Make `apply_preallocation` op-agnostic.  In `src/vmm/src/main.rs`, change its signature to add an `op_name: &str` argument that gets used in error messages (replacing the hard-coded `"create:"` prefix).  Refactor `fill_zeros` to expose a `fill_zeros_inner(fd, offset, length, fallocate_fn)` taking a closure; the public `fill_zeros` calls it with the real `libc::fallocate`.  Update the two existing `apply_preallocation` call sites in `run_create_nonraw` to pass `"create"`.  Add unit tests for `fill_zeros_inner` covering (a) the fast path (closure returns success), (b) the EOPNOTSUPP fallback (closure returns `Err(EOPNOTSUPP)` → write-loop fires + verifies bytes are zero on a tempfile), (c) the unrelated-error path (closure returns `Err(EIO)` → bubbles up).  `make instar`, `make lint`, `make test-rust`, `pre-commit run --all-files` clean. |
| 9b | medium | sonnet | none | Wire the resize post-pass.  In `run_resize_raw` (in `src/vmm/src/main.rs`), replace the existing `falloc`/`full`/`metadata` rejection with: reject `metadata` for raw with the message documented above; reject `falloc`/`full` + shrink as meaningless; otherwise call `apply_preallocation(&file, "resize", mode, current_virtual_size, added)` after `set_len` and before `sync_all`, where `added = new_virtual_size.saturating_sub(current_virtual_size)`.  In `run_resize_nonraw`, replace the `falloc`/`full` rejection: open the output file with read+write (not write-only as it is today), call `apply_preallocation(&file, "resize", mode, result.file_size_before, result.file_size_after - result.file_size_before)` after the guest finishes and before the final `set_len(result.file_size_after)`.  Reject `falloc`/`full` + shrink for non-raw similarly.  Verify `make instar` builds.  Add a smoke test that constructs a `ResizeArgs` with each preallocation mode and runs `Cli::try_parse_from` (no actual KVM launch — just verifies clap still accepts every mode). |

## Out of scope for phase 9

- Per-format data-region preallocation walks for sparse
  formats (qemu's actual behaviour for `falloc`/`full` on
  qcow2 / vhdx / vmdk / vhd-dynamic).  Queued under master-
  plan Future work.
- Lifting the planner's `PreallocationUnsupported` rejection
  for qcow2 `metadata` mode (master-plan Future work).
- Cross-version baselines (phase 10).
- End-to-end integration tests against real images (phase 11).
- Differential fuzzing (phase 12).
- Documentation, CHANGELOG, follow-ups (phase 13).

## Success criteria for phase 9

- `cargo build -p instar` clean.
- `make instar` builds.
- `make lint`, `pre-commit run --all-files` clean.
- `make test-rust` passes; new `fill_zeros_inner` tests
  succeed.
- `instar resize -f raw --preallocation=falloc foo.raw +1G`
  succeeds end-to-end (verified manually if `/dev/kvm` is
  available; otherwise verified by phase 11).
- `--preallocation=falloc` + shrink rejected with the
  documented message.
- `--preallocation=metadata` for raw rejected with the
  documented message.
- `apply_preallocation`'s `"create:"` prefix is replaced
  with the caller's `op_name`; both `run_create_nonraw` and
  the resize call sites pass the right name.

## Sub-agent guidance

Read these files before starting any step:

- `src/vmm/src/main.rs:8432-8549` (`fill_zeros` +
  `apply_preallocation`).
- `src/vmm/src/main.rs:7191-7231` (the `run_create_nonraw`
  call site that currently passes `"create"`-style error
  prefixes through the existing helper).
- `src/vmm/src/main.rs::run_resize_raw` (the function whose
  rejection branch phase 9 replaces).
- `src/vmm/src/main.rs::run_resize_nonraw` (same, for
  non-raw).
- `src/vmm/src/main.rs::ResizeArgs` (the preallocation field
  is already there from 8a; nothing to add).

The management session review checklist is the same as prior
phases.
