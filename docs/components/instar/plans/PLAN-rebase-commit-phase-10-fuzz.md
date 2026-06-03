# PLAN-rebase-commit phase 10: coverage-guided fuzzing

## Prompt

Before responding to questions or discussion points in this
document, explore the codebase thoroughly. Read the existing
coverage-guided fuzz harnesses in `src/fuzz/fuzz_targets/`,
specifically `fuzz_resize_planners.rs` (the structural twin
for this phase — it ships the per-planner invariant pattern
phase 10 mirrors for rebase + commit), the rebase planner
crate at `src/crates/rebase/src/lib.rs` /
`src/crates/rebase/src/qcow2.rs` / `vmdk.rs`, the commit
planner crate at `src/crates/commit/src/lib.rs` / `qcow2.rs`
/ `vmdk.rs`, the fuzz workspace's `Cargo.toml` and `lib.rs`,
the `coverage-fuzz.yml` CI workflow's `TARGETS` array and
per-target runner, and the master plan at
`docs/plans/PLAN-rebase-commit.md`. Ground your answers in
what the code actually does today.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-rebase-commit-phase-NN-<descriptive>.md`. The master
plan is [PLAN-rebase-commit.md](/components/instar/plans/PLAN-rebase-commit/). This
phase is the tenth of twelve.

I prefer one commit per logical step. The step table below
identifies three steps; this phase can land step by step or
as a single consolidated commit.

## Situation

The repository already ships fifteen coverage-guided fuzz
targets under `src/fuzz/fuzz_targets/`, all of them
libFuzzer-based and built via `cargo fuzz`. The most recent
addition is `fuzz_resize_planners.rs` (PLAN-resize step 12a,
shipped as `29e62da`), which is the **structural twin** for
phase 10: it decodes a 32-byte structured header into a
`(format_selector, opts, synthetic-existing-state slices)`
tuple, dispatches to one of the five `plan_resize_*`
functions, and on every successful return asserts plan-
level invariants (patch count bound, no integer overflow,
every patch's range within `total_file_size`, no overlapping
`Write` patches). Errors are silently ignored — libFuzzer's
only oracle is panic.

Phase 10 adds the equivalent targets for the rebase and
commit planners shipped in phases 2 and 6:

- **`fuzz_rebase_planners`** exercises `plan_rebase_qcow2`
  and `plan_rebase_vmdk` across both modes
  (`RebaseMode::Unsafe` and `RebaseMode::Safe`). Unsafe mode
  returns a `RebasePlan` of patches the host applies
  directly; safe mode returns a context the guest threads
  through a per-cluster comparison loop plus a deferred
  metadata `RebasePlan`. Both arms have invariants to assert.
- **`fuzz_commit_planners`** exercises `plan_commit_qcow2`
  and `plan_commit_vmdk`. Commit has only one mode (no
  Unsafe/Safe enum); the planners return a `Qcow2CommitContext`
  / `VmdkCommitContext` borrowing into scratch.

The relevant existing infrastructure this phase builds on:

- **`fuzz_resize_planners.rs`** — the reference shape for
  the planner-dispatch fuzz target. Header-decoded options +
  per-format slice carving + scratch reuse via `thread_local!`
  + invariant assertions in a helper function. Phase 10's two
  targets each follow this shape with the planner-specific
  invariant set.

- **`src/fuzz/Cargo.toml`** carries one `[[bin]]` entry per
  target. Phase 10 adds two more (`fuzz_rebase_planners` and
  `fuzz_commit_planners`).

- **`.github/workflows/coverage-fuzz.yml`** carries a
  `TARGETS` bash array (line 211–229) listing every fuzz
  target the workflow runs. Phase 10 appends two entries.

- **Plan-level invariants** the rebase + commit planners
  document via type constraints and Result discipline:
  - `MAX_REBASE_PATCHES = 16` cap on `RebasePlan.patches()`.
  - `MAX_COMMIT_PATCHES = 16` cap on `CommitPlan.patches()`
    (currently unused by the v1 commit planners since they
    return a context, not a patch list; the cap is reserved
    for future use).
  - Every patch's `byte_offset + len` must not overflow
    `u64`.
  - Safe-mode rebase's `RebaseQcow2SafeContext` carries
    `refblocks: &mut [u8]` and `dirty: &mut [u8]`; the
    dirty bitmap length must equal
    `(refblock_count + 7) / 8`.
  - Commit's `Qcow2CommitContext` carries
    `backing_refblocks: &mut [u8]` and `backing_dirty: &mut
    [u8]` with the same per-refblock bitmap relation.

## Mission and problem statement

After phase 10 lands:

1. **`src/fuzz/fuzz_targets/fuzz_rebase_planners.rs`**
   exists. The harness:
   - Decodes a structured header into a
     `(format_selector, mode_selector, opts,
     synthetic-existing-state slices, refblock-host-offset
     indices, refcount-block bytes, descriptor bytes,
     grain-table bytes, …)` tuple.
   - Dispatches to either `plan_rebase_qcow2` or
     `plan_rebase_vmdk` with the matching opts.
   - On `Ok(Qcow2RebaseOutput::Unsafe { plan })` or
     `Ok(VmdkRebaseOutput::Unsafe { plan })`: asserts the
     plan-level invariants (count, overflow, ranges, no
     overlapping Writes).
   - On `Ok(Qcow2RebaseOutput::Safe { context,
     deferred_metadata })`: asserts the same plan
     invariants on `deferred_metadata`, plus the context
     invariants (dirty-bitmap length equals
     `(refblock_count + 7) / 8`).
   - On any error: silently returns (libFuzzer panic-only
     oracle).

2. **`src/fuzz/fuzz_targets/fuzz_commit_planners.rs`** exists.
   The harness:
   - Decodes a structured header into a
     `(format_selector, opts, …)` tuple where the synthetic
     existing-state slices and refblock host offsets are
     carved from the post-header pool.
   - Dispatches to either `plan_commit_qcow2` or
     `plan_commit_vmdk`.
   - On `Ok(context)`: asserts the context invariants
     (dirty-bitmap length relation; staged refblock byte
     length matches `backing_refblock_count *
     backing_cluster_size`; the format-specific scalar
     fields look sane — sizes non-zero where the type
     requires).
   - On error: silently returns.

3. **`src/fuzz/Cargo.toml`** carries the two new `[[bin]]`
   entries with `rebase` and `commit` as planner-crate
   dependencies. The fuzz harness build target list
   (`cargo fuzz list` inside the workspace) reports both
   new targets.

4. **`.github/workflows/coverage-fuzz.yml`**'s `TARGETS`
   array carries `fuzz_rebase_planners` and
   `fuzz_commit_planners` so the nightly + on-demand CI
   runs include them.

5. Both targets build clean under `cargo fuzz build`
   inside the devcontainer (`make fuzz-build
   FUZZ_TARGET=fuzz_rebase_planners` and same for commit).
   Both targets survive a short local run
   (`make fuzz-run FUZZ_TARGET=fuzz_rebase_planners
   FUZZ_DURATION=60`) without crashing — short-budget runs
   exercise the harness's decode + dispatch shape but not
   the bug-finding power of long CI runs.

6. The execution-table row for phase 10 in
   `PLAN-rebase-commit.md` is marked Complete with the
   shipping commit hashes.

## Open questions

### 1. One combined target or two separate?

Working choice: **two separate targets**
(`fuzz_rebase_planners` and `fuzz_commit_planners`).

Rationale: the existing per-planner-family pattern
(`fuzz_resize_planners`, `fuzz_create_emitters`,
`fuzz_measure_calc`, etc.) keeps each target's input
budget focused on its own dispatch tree. Combining rebase
and commit into one target would waste libFuzzer's coverage
signal on a meta-dispatch byte that doesn't drive any
interesting code path. The Cargo + CI overhead of a second
target is negligible (one extra `[[bin]]` entry + one
extra line in `TARGETS`).

### 2. What invariants does each harness assert?

Working choice — mirror `fuzz_resize_planners`'s set with
planner-specific additions:

**For `RebasePlan` (unsafe-mode output, safe-mode's
`deferred_metadata`):**

- Patch count `<= MAX_REBASE_PATCHES` (16).
- Every patch's `byte_offset + len` doesn't overflow u64.
- Every `Write` / `ZeroFill` patch's range lies within
  `overlay_file_size` (the rebase analogue of resize's
  `total_file_size`).
- `Append`s end at or below the post-append `overlay_file_size`.
- No two `Write` patches overlap.

**For safe-mode `RebaseQcow2SafeContext`:**

- `dirty.len() == (refblock_count + 7) / 8`.
- `refblocks.len() == refblock_count * cluster_size`
  (cluster_size echoed from the parsed header).
- `entries_per_refblock == cluster_size * 8 / refcount_bits`.

**For `Qcow2CommitContext` / `VmdkCommitContext`:**

- `backing_dirty.len() == (backing_refblock_count + 7) / 8`
  (qcow2 only).
- `backing_refblocks.len() == backing_refblock_count *
  backing_cluster_size` (qcow2 only).
- `overlay_entries_per_refblock == overlay_cluster_size * 8
  / overlay_refcount_bits` (qcow2 only).
- For vmdk: `overlay_grain_size > 0`,
  `overlay_num_gd_entries > 0`, `gd_offset_sectors > 0`.

Errors (`RebaseError::*`, `CommitError::*`) are silently
ignored — libFuzzer's only oracle is panic.

### 3. Corpus seeds?

Working choice: **no seed corpus**. The resize planner
target shipped without one and the nightly runs surface
new coverage organically. Phase 10 follows that pattern.

If a future bug-hunt session wants targeted seeds, the
`scripts/generate-fuzz-seeds.py` infrastructure (PLAN-fuzz's
seed-discovery work) can pre-generate inputs from the
existing testdata; that's a follow-up.

### 4. Scratch buffer sizing

`fuzz_resize_planners` sizes its `thread_local! SCRATCH`
to `QCOW2_MAX_RESIZE_SCRATCH` (the largest per-format
worst case). Phase 10 follows the same pattern:

- `fuzz_rebase_planners`: scratch sized to the larger of
  qcow2's and vmdk's worst case (the rebase planner crate
  exposes a `MAX_REBASE_SCRATCH` constant or computes the
  bound; the harness can use a generous static cap like
  16 MiB if no published constant exists).
- `fuzz_commit_planners`: same approach; the commit
  planner reserves scratch for staging backing-refcount-
  blocks + the dirty bitmap, bounded by
  `MAX_REFBLOCKS * cluster_size + dirty bytes`.

Both targets keep the scratch buffer alive across
invocations via `thread_local!`.

### 5. Mode selection for rebase

Rebase has two modes (`Unsafe` / `Safe`). Working choice:
the harness's header byte selects between them. The unsafe
arm exercises the metadata-only rewrite path; the safe arm
exercises the cluster-aware allocator + deferred-metadata
emission path. Both arms reuse the same opts skeleton with
the safe-mode-only fields populated from the post-header
pool when the safe arm fires.

### 6. Format selection

Working choice: a 1-byte format selector at the head of the
input (`data[0] % 2` for the qcow2/vmdk pick). The
remaining bits of `data[0]` are reserved for future
sub-format selection.

### 7. Header size

Working choice: **32 bytes** to match
`fuzz_resize_planners`. Carries the format + mode
selectors, flag byte, cluster/grain size, virtual-size
fields, overlay-file-size, backing-virtual-size,
refcount-table-offset hint, and refblock-count hint. The
remaining input bytes form the slice pool.

### 8. CI integration timing

Working choice: **append both targets to the
`coverage-fuzz.yml` `TARGETS` array in step 10c**. The
nightly run extends by ~2 × `FUZZ_DURATION` seconds (with
the default 600s budget, that's an extra 20 minutes on
the nightly).

If the operator wants to shorten the per-target budget
for the new entries, the workflow already supports a
comma-separated `FUZZ_TARGETS` workflow_dispatch input
that overrides the default list; no per-target budget
parameter exists today.

### 9. Differential fuzzing vs qemu-img — defer

Phase 11 ships the rebase + commit arms of the differential
fuzzer (`scripts/differential-fuzz.py`). Phase 10's targets
are coverage-guided only — they assert internal invariants,
not equivalence with qemu-img.

## Execution

The phase plan recommends three steps. Each step is small
enough to review independently; consolidating into one or
two commits at the end is also fine.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 10a | medium | sonnet | none | `src/fuzz/fuzz_targets/fuzz_rebase_planners.rs` + matching `[[bin]]` entry in `src/fuzz/Cargo.toml` (with `rebase` as a path dependency). Decode header into `(format_selector, mode_selector, flags, sizes, …)`, carve per-format slices from the post-header pool, dispatch to `plan_rebase_qcow2` or `plan_rebase_vmdk` with both modes reachable, assert `RebasePlan` invariants (mirroring `fuzz_resize_planners`'s `assert_invariants`) on `Unsafe` and on safe-mode's `deferred_metadata`, and the safe-mode context's dirty-bitmap length relation. Mirror the resize target's scratch-reuse via `thread_local!`. `make fuzz-build FUZZ_TARGET=fuzz_rebase_planners` succeeds; `make fuzz-run FUZZ_TARGET=fuzz_rebase_planners FUZZ_DURATION=30` runs clean. |
| 10b | medium | sonnet | none | `src/fuzz/fuzz_targets/fuzz_commit_planners.rs` + matching `[[bin]]` entry in `src/fuzz/Cargo.toml` (with `commit` as a path dependency). Decode header into `(format_selector, flags, sizes, refblock_count_hint, …)`, carve per-format slices from the post-header pool, dispatch to `plan_commit_qcow2` or `plan_commit_vmdk`, assert the context invariants (dirty-bitmap length relation; staged refblock byte length matches `refblock_count * cluster_size`; the format-specific scalar fields are coherent). Mirror the rebase target's scratch-reuse pattern. `make fuzz-build` + `make fuzz-run` for the new target run clean. |
| 10c | low | sonnet | none | Append `fuzz_rebase_planners` and `fuzz_commit_planners` to the `TARGETS` array in `.github/workflows/coverage-fuzz.yml`. Pre-commit clean. Master plan updated to mark phase 10 complete with the shipping commit hashes. Document any invariants caught + harness tweaks during 10a/10b in this phase plan's "Bugs fixed" / future-work sections. |

## Agent guidance

### Execution model

Same model as phases 1–9: implementation work runs in the
management session unless explicitly delegated. The model
guidance in the step table reflects what a sub-agent would
need *if* this work were delegated; the management session
should reach for opus if the per-format opts construction
turns out to need more substantial massaging than the
resize template suggests.

### Planning effort

The master plan flagged this phase as **medium effort**.
Within the phase, 10a and 10b are both medium (each is
~150–200 LoC of harness with format-specific opts
construction); 10c is low.

### Step ordering

Strict dependency: 10a → 10b → 10c. 10b can interleave
with 10a since they touch different files, but the
natural review order is 10a (rebase, the more complex
opts surface) then 10b (commit, slightly simpler) then
10c (CI wiring).

### Management session review checklist

After each step:

- [ ] The files that were supposed to change actually
      changed.
- [ ] No unrelated files modified.
- [ ] `cargo fuzz build` for the new target succeeds
      inside the devcontainer.
- [ ] `cargo fuzz run --max_total_time=30` for the new
      target runs without crashing.
- [ ] `make instar` / `make lint` / `make test-rust` /
      `pre-commit run --all-files` all pass — fuzz
      targets aren't part of the main build but their
      Cargo.toml lives in the workspace, so any
      manifest typo would surface here.
- [ ] The CI `TARGETS` array contains both new entries
      after 10c.

## Administration and logistics

### Success criteria

Phase 10 is complete when:

* The two new fuzz targets exist and build clean.
* Both targets survive a 30-second local run without
  panic.
* `make instar`, `make lint`, `make test-rust`, and
  `pre-commit run --all-files` are all clean.
* The CI workflow lists both targets in `TARGETS`.
* The execution-table row for phase 10 in
  `PLAN-rebase-commit.md` is marked Complete with the
  shipping commit hashes.

### Future work created by this phase

Anticipated; the implementation may surface more.

- **Targeted seed corpus** for both new targets. The
  `scripts/generate-fuzz-seeds.py` infrastructure can
  walk the existing testdata and emit
  `(starting_header_bytes, backing_metadata, options)`
  tuples for rebase and commit. Out of scope for v1.
- **Coverage report differential** comparing the
  per-version coverage of the new targets against the
  existing resize/create/measure targets. PLAN-fuzz's
  coverage-tracking work tracks this; if phase 10's
  targets show suspiciously low coverage on a known
  code path, the harness needs a header-decode tweak.
- **Differential fuzzing**: phase 11 ships the rebase +
  commit arms of `scripts/differential-fuzz.py`. Phase
  10's targets are coverage-guided only.
- **Per-target budget tuning**: the nightly run currently
  uses a single `FUZZ_DURATION` across all targets. If
  rebase + commit need a different budget than the
  existing targets (the rebase safe-mode path covers more
  branches than unsafe-mode), `coverage-fuzz.yml` would
  need a per-target budget map. Out of scope for v1.

### Bugs fixed during this work

- **`plan_rebase_vmdk` unsafe-mode `RebasePlan::new(0)`**
  (`src/crates/rebase/src/vmdk.rs`, fixed in `1611841`).
  The vmdk unsafe-mode planner was constructing its
  returned `RebasePlan` via `RebasePlan::new(0)`, leaving
  `total_file_size` at zero regardless of the actual
  overlay file size. The `qcow2` unsafe-mode planner
  correctly uses `RebasePlan::new(opts.overlay_file_size)`;
  this brought the vmdk planner in line. Surfaced by
  `fuzz_rebase_planners` on its very first run with a
  panic on the plan-level invariant `patch.end <=
  total_file_size` against any real-image-sized descriptor
  offset. The descriptor rewrite mutates an existing
  region in place; the file size doesn't change.

### Documentation index maintenance

Not added to `docs/plans/order.yml` — phase plans live
alongside the master plan but only the master plan is
indexed.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
