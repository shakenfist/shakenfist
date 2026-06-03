# PLAN-rebase-commit phase 11: differential fuzzing vs qemu-img

## Prompt

Before responding to questions or discussion points in this
document, explore the codebase thoroughly. Read the existing
differential fuzzer at `scripts/differential-fuzz.py` —
particularly the `OPERATIONS` list, the `op_*` function
contracts, the `_*_option_picker` helpers, the `run_iteration`
dispatch (around line 1415), the `compare_exit_codes` /
`_strip_divergent_fields` / `_normalise_create_info` shared
helpers, and the existing `op_resize` (the structural twin
this phase mirrors most closely). Read the
`.github/workflows/differential-fuzz.yml` CI workflow to
understand how iteration counts and timeouts are picked per
trigger. Read the phase 8 commit smoke tests in
`tests/test_commit.py` and the phase 5 rebase smoke tests in
`tests/test_rebase.py` to understand the known per-format
constraints (vmdk implicit-`-b` gap, qcow2 long-path
relocation, vmdk descriptor slot size, etc.) that the
option pickers must honour to avoid flagging documented
gaps as divergences. Ground your answers in what the code
actually does today.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-rebase-commit-phase-NN-<descriptive>.md`. The master
plan is [PLAN-rebase-commit.md](/components/instar/plans/PLAN-rebase-commit/). This
phase is the eleventh of twelve.

I prefer one commit per logical step. The step table below
identifies three steps; this phase can land step by step or
as a single consolidated commit.

## Situation

The repository already ships `scripts/differential-fuzz.py`,
a Python-driven differential fuzzer that for every iteration:

1. Picks a random seed (logged for reproducibility).
2. Generates a random disk image via `qemu-img create`.
3. Runs a random chain of 2–4 operations independently against
   `instar` and `qemu-img` on separate copies of the same input.
4. Compares outputs after each operation; exits with a
   structured divergence record on the first unexplained
   mismatch.

The `OPERATIONS` list at line 48 of `differential-fuzz.py`
currently contains `info`, `check`, `convert`,
`convert_compressed`, `measure`, `create`, `resize`. Each has
a paired `op_<name>` function (line 565 onward) and, where
the operation needs randomised inputs, a paired
`_<name>_option_picker`. The newest addition is **`op_resize`**
(shipped in `6fb032c` as PLAN-resize step 12b) — that's the
structural twin for phase 11's work.

Phase 11 adds the rebase and commit arms:

- **`op_rebase`** drives `instar rebase` and `qemu-img rebase`
  against the same overlay+backing fixture, then compares
  the resulting overlay's `qemu-img info --output=json`
  through the same `_strip_divergent_fields` normaliser the
  other ops use. The picker biases inputs away from known
  documented gaps:
  - qcow2 only (qemu-img rebase rejects every other format).
  - Mode mix between `-u` (always supported) and safe mode
    (supported by both binaries; the picker avoids the
    long-path relocation case until that planner gap lifts).
  - The new-backing path is constrained to fit the overlay's
    existing slot.

- **`op_commit`** drives `instar commit` and `qemu-img commit`
  against the same overlay+backing fixture, then compares
  both the overlay's and the backing's
  `qemu-img info --output=json` (a commit's observable state
  lives on both sides). The picker:
  - qcow2 always; vmdk only via explicit `-b BASE` (the
    implicit-`-b` gap blocked by the info-vmdk-backing-file
    follow-up).
  - Optional `qemu-io` seed at offset 0 so the commit has
    real data to merge.

Both ops slot into `run_iteration`'s dispatch the same way
`op_resize` does — through the `OPERATIONS` list and the
`if op == '<name>':` branch at line 1439.

The relevant existing infrastructure this phase builds on:

- **`compare_exit_codes`** (line 485) — Returns a divergence
  dict or `None`; reclassifies timeouts on either side as
  inconclusive (the rebase + commit ops inherit this for
  free).

- **`_strip_divergent_fields`** (line 436) — strips
  `actual-size`, `image-clusters`, etc. before comparing
  info JSONs. Both new ops reuse it via
  `_normalise_create_info` (line 961, also used by
  `op_resize`).

- **`run_iteration`** (line 1415) — uniform `op_*(instar_bin,
  instar_copy, qemu_copy, fmt, timeout, ...)` signature.
  `op_resize` and `op_create` use the `instar_copy / qemu_copy /
  fmt` args as placeholders only and build their own fixture
  pair; `op_rebase` and `op_commit` follow the same pattern.

- **`.github/workflows/differential-fuzz.yml`** — runs inside
  the `instar-build` Docker container with `/dev/kvm` mapped
  in so the rebase + commit guest binaries can launch. No
  workflow-level changes are required — the new ops are
  reached purely through the updated `OPERATIONS` list.

- **Phase 5 / phase 8 / phase 9 smoke + matrix tests** at
  `tests/test_rebase.py` and `tests/test_commit.py` —
  document the per-format constraints the pickers must
  honour. The smoke tests already gate on the same known
  gaps (vmdk implicit-`-b` skipTest, long-path
  relocation refusal, intermediate-image refusal) that the
  pickers must bias around.

## Mission and problem statement

After phase 11 lands:

1. `scripts/differential-fuzz.py`:
   - `OPERATIONS` list carries `'rebase'` and `'commit'`.
   - `_rebase_option_picker(rng)` returns `(overlay_size,
     new_backing_name, new_backing_size, mode_flags,
     extra_create_options)` honouring the constraints in
     open question 2.
   - `_commit_option_picker(rng)` returns `(target,
     overlay_size, explicit_base, seed_spec,
     extra_create_options)` honouring the constraints in
     open question 3.
   - `op_rebase(instar_bin, instar_copy, qemu_copy, fmt,
     timeout, rng)`: builds two byte-identical overlay+backing
     pairs (one for instar, one for qemu-img) via `qemu-img
     create`, runs the respective rebase on each, compares the
     resulting overlay's info JSON. Returns a divergence dict
     or `None`. Same exit-code-comparison + info-JSON
     normalisation pattern as `op_resize`.
   - `op_commit(instar_bin, instar_copy, qemu_copy, fmt,
     timeout, rng)`: builds two byte-identical
     overlay+backing pairs in per-pair subdirectories, runs
     the respective commit on each, compares both the
     overlay's and the backing's info JSON.

2. `run_iteration` dispatches to both new ops through the
   uniform `if op == 'rebase'` / `if op == 'commit'`
   branches.

3. A 100-iteration local run against the head of `develop`
   reports zero divergences (or only inconclusive timeouts
   when qemu-img hangs).

4. `make instar`, `make lint`, `pre-commit run --all-files`
   are all clean. `make test-rust` doesn't regress.

5. The execution-table row for phase 11 in
   `PLAN-rebase-commit.md` is marked Complete with the
   shipping commit hashes.

## Open questions

### 1. CI workflow changes required?

Working choice: **no workflow-level changes**. The new ops
are wired in purely through the Python `OPERATIONS` list. The
`differential-fuzz.yml` workflow already runs inside the
`instar-build` Docker container with `/dev/kvm` mapped in,
which is what the rebase + commit guest binaries need. The
existing iteration counts (100 PR / 200 push / 1000 nightly)
extend naturally — the picker is constrained tightly enough
that each rebase/commit op takes a fraction of a second.

### 2. `_rebase_option_picker` constraints

Working choice — bias around the documented gaps:

```python
def _rebase_option_picker(rng):
    """Pick (overlay_size, new_backing_name, new_backing_size,
    rebase_flags, extra_create_options).

    Constraints honour the rebase planner's documented gaps:
      * qcow2 only (qemu-img rebase rejects vmdk / vhd /
        vhdx with "Operation not supported"; the differential
        surface has nothing to compare against).
      * No long-path relocation (qcow2 BACKING_PATH_TOO_LONG
        from the planner; phase 4 + phase 5 documented gap).
        Both backing names match the overlay's existing slot
        size, picked from a small set.
      * No qcow2 refcount_bits != 16 (instar hardcodes).
      * No qcow2 compat=0.10 (instar hardcodes 1.1).
      * Mode mix between `-u` (always supported) and safe mode
        (both binaries support qcow2 safe-mode).
    """
    overlay_size = rng.choice(['1M', '4M', '64M'])
    new_backing_size = rng.choice(['1M', '4M', '64M'])
    # Backing names must match the original slot's basename
    # length so the rebase doesn't trip the long-path planner
    # gap. The fixture generator hard-codes the original
    # backing's name to a 10-char string.
    new_backing_name = rng.choice(['next.qcow2',
                                    'base.qcow2',
                                    ''])  # detach
    is_detach = new_backing_name == ''
    mode = rng.choice(['unsafe', 'safe'])
    flags = []
    if mode == 'unsafe':
        flags.append('-u')
    if not is_detach:
        flags += ['-F', 'qcow2']
    create_options = []
    # qcow2 cluster_size mix biased away from 2 MiB (resize
    # picker excludes it for related scratch-budget reasons;
    # rebase doesn't have the same bound, but keep the
    # picker tight for runtime).
    cs = rng.choice([512, 4096, 65536, 262144])
    create_options.append(f'cluster_size={cs}')
    if rng.random() < 0.3:
        create_options.append('lazy_refcounts=on')
    return overlay_size, new_backing_name, new_backing_size, \
           flags, create_options
```

### 3. `_commit_option_picker` constraints

Working choice — bias around phase 8/9's gaps:

```python
def _commit_option_picker(rng):
    """Pick (target, overlay_size, explicit_base, seed_spec,
    extra_create_options).

    Constraints:
      * qcow2 + vmdk supported (qemu-img commit accepts both).
      * vmdk implicit-`-b` blocked by the
        info-vmdk-backing-file follow-up; vmdk cases always
        pass `explicit_base = 'base.vmdk'`.
      * qcow2 cluster_size_mismatch refusal applies to both
        binaries — when picking cluster_size, use the same
        value for the backing's implicit create_options too
        (the fixture generator does this automatically).
      * No qcow2 refcount_bits != 16, no compat=0.10.
      * Optional seed via `qemu-io -c 'write -P 0xab 0 64k'`
        before the commit so the differential has real data
        to merge.
    """
    target = rng.choice(['qcow2', 'vmdk'])
    overlay_size = rng.choice(['1M', '4M', '64M'])
    if target == 'vmdk':
        explicit_base = 'base.vmdk'  # implicit -b blocked
    else:
        # qcow2: mix implicit and explicit.
        explicit_base = rng.choice([None, 'base.qcow2'])
    seed_spec = rng.choice([None, 'seed-64k'])
    create_options = []
    if target == 'qcow2':
        cs = rng.choice([512, 4096, 65536, 262144])
        create_options.append(f'cluster_size={cs}')
        if rng.random() < 0.3:
            create_options.append('lazy_refcounts=on')
    return target, overlay_size, explicit_base, seed_spec, \
           create_options
```

### 4. Per-pair subdirectory for `op_commit`?

Working choice: **yes** — same reason as the phase 9 round-trip
tests. `qemu-img commit -b BASE` canonicalises the chain
entry relative to the overlay's directory. Without per-pair
subdirs the basename-vs-absolute-path matching that
phase 9's test_commit.py uses doesn't apply uniformly to a
fuzzer-built fixture. Each side (`instar` / `qemu-img`)
gets its own per-pair subdir under the iteration's tmpdir.

### 5. Comparison strategy

Working choice — mirror `op_resize`'s pattern:

1. Compare exit codes via `compare_exit_codes` (existing
   helper; reclassifies timeouts as inconclusive).
2. If both succeed, run `qemu-img info --output=json` on
   each instar-side file, normalise via
   `_normalise_create_info`, compare. Any mismatch is a
   structured divergence record.
3. For commit: do the same comparison on the BACKING side.
   Two parallel comparisons, two structured divergence types
   (`commit_overlay_info_divergence`,
   `commit_backing_info_divergence`).

### 6. Detach in `op_rebase`?

Working choice: **yes**. The picker emits `new_backing_name
= ''` ~25% of the time (the `''` branch in the qcow2 case
above). qemu-img rebase accepts detach via `-b ''`; instar
rebase does too (the phase 4 `Commands::Rebase` parses the
empty string). Both sides should produce overlays with no
`backing-filename` in the info JSON; the comparison is the
same shape.

### 7. Skipping inconclusive iterations

`compare_exit_codes` (line 485) already returns
inconclusive-with-context when either side times out. Both
new ops inherit that behaviour by calling it. The fuzzer's
existing summary already breaks out
`{ok, divergent, inconclusive}` counts.

### 8. `qemu-io` availability inside the container

The `instar-build` container ships qemu utilities (used by
the existing convert/resize ops and the rebase/commit
matrix tests). `qemu-io` is part of the same package set;
`op_commit` calls it directly when `seed_spec ==
'seed-64k'`. If the binary is missing for some reason, the
op records a structured skip rather than failing the fuzz
iteration.

### 9. Future-work: intermediate-image commit cases

The picker's qcow2 commit cases match the overlay's
immediate parent only. Once phase 8's intermediate-image
commit follow-up lands, the picker can grow a third tuple
field naming a deeper ancestor; the comparison code stays
unchanged.

## Execution

The phase plan recommends three steps. Each step is small
enough to review independently; consolidating into one or
two commits at the end is also fine.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 11a | medium | sonnet | none | Add `_rebase_option_picker(rng)` and `op_rebase(instar_bin, instar_copy, qemu_copy, fmt, timeout, rng)` to `scripts/differential-fuzz.py`. Constraints + comparison shape per open questions 2 + 5 + 6. Append `'rebase'` to `OPERATIONS` and the `elif op == 'rebase'` arm in `run_iteration`. A 100-iteration local run reports zero non-inconclusive divergences. |
| 11b | medium | sonnet | none | Add `_commit_option_picker(rng)` and `op_commit(instar_bin, instar_copy, qemu_copy, fmt, timeout, rng)` to `scripts/differential-fuzz.py`. Per-pair subdirectory layout per open question 4. Two parallel comparisons (overlay + backing) per open question 5. Optional `qemu-io` seed step per open question 8. Append `'commit'` to `OPERATIONS` and the `elif op == 'commit'` arm in `run_iteration`. A 100-iteration local run reports zero non-inconclusive divergences. |
| 11c | low | sonnet | none | Pre-commit clean. Master plan updated to mark phase 11 complete with shipping commit hashes. Document anything that surfaced during 11a/11b in this phase plan's "Bugs fixed" / future-work sections. |

## Agent guidance

### Execution model

Same model as phases 1–10: implementation work runs in the
management session unless explicitly delegated. The model
guidance in the step table reflects what a sub-agent would
need *if* this work were delegated; the management session
can stick with sonnet for both 11a and 11b — the work is
mostly translating the resize/create ops into
rebase/commit-shaped equivalents.

### Planning effort

The master plan flagged this phase as **medium effort**.
Within the phase, 11a and 11b are both medium (each ~150
LoC of harness with format-specific picker + fixture
generation + comparison); 11c is low.

### Step ordering

Strict dependency: 11a → 11b → 11c. 11a and 11b can
interleave since they touch different new functions, but
the natural review order is 11a (rebase — closer to
`op_resize`'s shape) then 11b (commit — adds the per-pair
subdirectory wrinkle).

### Management session review checklist

After each step:

- [ ] The files that were supposed to change actually
      changed.
- [ ] No unrelated files modified.
- [ ] `python3 scripts/differential-fuzz.py --instar
      src/target/release/instar --iterations 100 --seed
      <fixed-seed>` runs to completion.
- [ ] The summary at the end shows `divergent: 0` (or only
      inconclusive timeouts).
- [ ] `make instar`, `make lint`, `pre-commit run
      --all-files` all pass.

## Administration and logistics

### Success criteria

Phase 11 is complete when:

* `OPERATIONS` carries both new ops.
* A 100-iteration local run with a fixed seed reports zero
  divergences.
* `make instar`, `make lint`,
  `pre-commit run --all-files`, and `make test-rust` are
  all clean.
* The execution-table row for phase 11 in
  `PLAN-rebase-commit.md` is marked Complete with the
  shipping commit hashes.

### Future work created by this phase

Anticipated; the implementation may surface more.

- **Intermediate-image commit cases**. Once phase 8's
  intermediate-image follow-up lands, extend
  `_commit_option_picker` with deep-chain cases (overlay
  → intermediate → base, `-b base.qcow2`). The comparison
  shape doesn't change.
- **vmdk implicit-`-b`**. Once the info-vmdk-backing-file
  follow-up lands, drop the explicit-`-b` constraint from
  the vmdk case in `_commit_option_picker`.
- **Long-path relocation in `_rebase_option_picker`**. The
  current picker keeps the new backing name's length
  matching the overlay's existing slot to avoid the
  long-path planner gap. Once the relocation planner is
  hardened, the picker can pick longer names.
- **Bug-class telemetry**. Both new ops file divergences
  through the existing `gh issue create` path (when run
  in CI with `--create-issues`). Triaging the divergence
  types into the existing severity buckets matches the
  resize/create pattern.

### Bugs fixed during this work

No new planner bugs surfaced during 11a/11b — both new
ops exercise the rebase/commit paths exhaustively tested
by phases 2–9, and the picker constraints documented
above keep the fuzzer inside the supported envelope.

### Picker constraints discovered during bring-up

The first 100-iteration run of 11b surfaced 22 divergences
across two documented gap categories — neither a planner
bug but both worth pinning in the picker:

- **vmdk explicit `-b`** (16 of 22 divergences): instar
  refused with "overlay has no recorded backing file" for
  every vmdk case. Root cause is the
  info-vmdk-backing-file follow-up (the host info operation
  doesn't surface vmdk monolithicSparse's
  `parentFileNameHint` via `backing_file`), already gated
  with `skipTest` in the phase 8e smoke + phase 9b matrix
  + phase 9c round-trip tests. Picker fix: drop `'vmdk'`
  from the commit target choice entirely. Once the info
  follow-up lands, add `'vmdk'` back.

- **qcow2 commit scratch budget** (6 of 22 divergences):
  instar's commit guest returned `ERROR_SCRATCH_TOO_SMALL`
  for `cluster_size > 64 KiB`. The commit guest carves
  `OVERLAY_RT_LIMIT = BACKING_RT_LIMIT = MAX_SECTOR_SIZE
  (64 KiB)`; a single-cluster refcount table for any
  `cluster_size > 64 KiB` blows that budget. Picker fix:
  cap `cluster_size` at 65536. Lifting the bound is a
  master-plan TODO — the resize phase's QCOW2_MAX_RESIZE_
  SCRATCH carries the equivalent ceiling for the same
  reason.

After both picker fixes, two consecutive 100-iteration runs
(seeds 42, 7777) report 0 divergences each.

### Documentation index maintenance

Not added to `docs/plans/order.yml` — phase plans live
alongside the master plan but only the master plan is
indexed.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
