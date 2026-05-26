# PLAN-resize phase 12: fuzz harnesses

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read the
existing fuzz infrastructure (`src/fuzz/Cargo.toml`, every
`src/fuzz/fuzz_targets/fuzz_*.rs`, especially
`fuzz_create_emitters.rs` which is the closest precedent;
`scripts/differential-fuzz.py` including `op_create` and
`_create_option_picker`; `.github/workflows/coverage-fuzz.yml`
and `.github/workflows/differential-fuzz.yml`). Understand the
resize planner's public API surface in
`src/crates/resize/src/lib.rs` (`Qcow2ResizeOpts`,
`VhdResizeOpts`, `VhdxResizeOpts`, `VmdkResizeOpts`,
`RawResizeOpts`, `ResizePlan`, `ResizePatch`, `ResizeError`).

Where a question touches on external concepts (libFuzzer
panics-as-oracle, cargo-fuzz semantics, qemu-img resize
behaviour at the matrix edges), research as needed to give a
confident answer. Flag any uncertainty explicitly rather than
guessing.

This is a phase plan under `PLAN-resize.md`. Refer to that
master plan for overall context. Phases 1–11 shipped the
planner, the guest binary, the host CLI + preallocation post-
pass, the cross-version baseline matrix, and the integration
test suite. Phase 12 adds fuzz coverage on top of all of
that.

## Mission

Ship two complementary fuzz harnesses:

1. **`src/fuzz/fuzz_targets/fuzz_resize_planners.rs`** —
   coverage-guided libFuzzer target that exercises the five
   `plan_resize_*` functions in `src/crates/resize/`. Decodes a
   structured 32-byte fuzzer-supplied header into a
   `(format_selector, opts, synthetic existing-state bytes)`
   tuple, dispatches to the matching planner, and asserts a
   bounded set of plan-level invariants on every successful
   return. Modelled tightly on `fuzz_create_emitters.rs`.

2. **`scripts/differential-fuzz.py` `op_resize`** — a new
   operation in the existing random-chain harness. Picks a
   `(target, start_size, end_spec, options, preallocation)`
   tuple from a curated picker (analogous to
   `_create_option_picker`), creates the same start image in
   two tempdirs (instar + qemu-img), runs `instar resize` and
   `qemu-img resize` against the matching copies, then
   compares via `qemu-img info --output=json` on both
   outputs. Honours the same divergence whitelist phase 11's
   matrix uses (`KNOWN_RESIZE_DIVERGENCES`) so documented gaps
   don't show up as fuzz divergences.

Together they cover two distinct surfaces: the coverage target
hunts panics / overflows / assertion failures in the planner
proper without needing qemu-img on the box; the differential
target catches *behavioural* divergences against qemu's writer
on the live system.

## What the survey turned up

- **`src/fuzz/Cargo.toml`** — 16 existing `[[bin]]` targets;
  workspace already depends on `qcow2`, `vmdk`, `vhd`, `vhdx`,
  `raw`, `create`, `measure`, `shared`, `luks`, but **not
  `resize`**. Phase 12a adds `resize = { path = "../crates/resize" }`
  plus a new `[[bin]]` entry for `fuzz_resize_planners`.
- **`fuzz_create_emitters.rs`** is the closest precedent
  (288 lines): structured-header parsing, per-format dispatch
  on the first byte, `thread_local!` reusable scratch +
  reparse buffers, four bookkeeping invariants on every
  successful plan, plus an optional re-parse round-trip
  guarded by a 16 MiB cap. The resize harness mirrors this
  shape exactly — same invariant style, same reusable scratch
  pattern, same `if let Ok(plan) = result` gate, same panic-
  only oracle.
- **The resize planner's `Opts` surface** is the harness's
  main complexity. `RawResizeOpts` is trivial (three
  numeric fields). `Qcow2ResizeOpts` has 12 fields, four of
  which are `&[u8]` slices (`existing_l1_bytes`,
  `existing_refcount_table_bytes`,
  `existing_refcount_block_bytes`,
  `existing_l2_bytes`) plus two index slices
  (`existing_refcount_block_indices`,
  `existing_l2_indices`). `VhdResizeOpts` /
  `VhdxResizeOpts` / `VmdkResizeOpts` each have ~10
  fields including `existing_header` / `existing_bat` /
  `existing_gd` slices. The harness needs to synthesise
  plausible byte buffers for each.
- **`ResizePlan`** is `Copy`, has up to
  `MAX_RESIZE_PATCHES = 128` patches, exposes
  `total_file_size` (u64) and `patches() -> &[ResizePatch]`.
  `ResizePatch` is a 3-variant enum: `Write` /
  `Append` / `ZeroFill`, each with a `byte_offset` and either
  bytes or a length. The invariant set is:
  - patch count ≤ 128
  - every patch end ≤ `total_file_size` (for `Write` /
    `ZeroFill`) — appends define EOF as they fire so the
    inequality is exactly equal-to-or-less-than after the
    plan completes
  - no two `Write` patches overlap
  - `byte_offset + len` doesn't overflow u64
- **`scripts/differential-fuzz.py`**'s `op_create`
  (1002–1088) and `_create_option_picker` (935–1000) are
  the precedents for the differential surface. `op_create`
  is ~85 lines: pick a tuple, run both tools, compare via
  `qemu-img info`. The resize equivalent is structurally
  identical but with an extra create step before the
  resize step on each side.
- **`KNOWN_RESIZE_DIVERGENCES`** at
  `tests/test_resize.py:34` captures the four documented
  resize-time divergences (`compat=0.10`, `refcount_bits=1`,
  `refcount_bits=64`, `qcow2 metadata preallocation`). The
  fuzzer must skip these tuples; phase 12 either imports the
  dict via a small Python loader, or duplicates the option
  whitelist in `_resize_option_picker` (the cleaner choice
  — Python script ergonomics, no path back into the test
  module).
- **vmdk / vhd / vhdx are out of scope for the differential
  surface**: phase 10 confirmed qemu-img doesn't support
  resize on those formats. The picker restricts to qcow2 +
  raw.
- **`.github/workflows/coverage-fuzz.yml`** (line 211–228)
  has a hard-coded TARGETS array of 16 entries; phase 12
  appends `fuzz_resize_planners` and bumps `N_TARGETS=16`
  to `17` at line 152. The per-target time budget
  recalculates automatically from `N_TARGETS`.
- **`.github/workflows/differential-fuzz.yml`** invokes
  `scripts/differential-fuzz.py` with a default operation
  set; the new `resize` op is picked up automatically once
  it's added to the `OPERATIONS` list at line 48.
- **README + ARCHITECTURE** both quote a "13 fuzz targets"
  count that is already stale (current actual: 16); phase
  12 brings it to 17 and updates both doc files.

## Algorithmic design

### Coverage target: `fuzz_resize_planners.rs`

Header layout (32 bytes — wider than create's 24 because
resize has more knobs):

```
offset  size  field
  0      1   format_selector       (0=raw, 1=qcow2 grow,
                                    2=qcow2 shrink, 3=vmdk,
                                    4=vhd, 5=vhdx, mod 6)
  1      1   flags                 (bit 0: allow_shrink,
                                    bit 1: extended_l2,
                                    bit 2: lazy_refcounts,
                                    bits 4-5: preallocation
                                              mode 0..3)
  2      1   refcount_bits_sel     (0..7 -> 1/2/4/8/16/32/64
                                              /0xff)
  3      1   subformat_sel         (used by vmdk/vhd)
  4      4   cluster_size / grain_size / block_size
                                   (raw bytes; planner clamps
                                    or rejects)
  8      8   current_virtual_size  u64
 16      8   new_virtual_size      u64
 24      8   current_file_size     u64 (planner field;
                                    independent of
                                    current_virtual_size
                                    on purpose so the
                                    fuzzer can probe the
                                    invariant)
```

Remaining bytes after offset 32 are the "synthetic
existing-state pool". For qcow2 the harness carves four
slices from this pool:
- `existing_l1_bytes` = first 4 KiB
- `existing_refcount_table_bytes` = next 4 KiB
- `existing_refcount_block_bytes` = next 32 KiB (= 4 cluster-
  sized blocks at the default 8 KiB cluster, or 2 at 16 KiB)
- `existing_l2_bytes` = next 32 KiB
- `existing_refcount_block_indices` = synthesised
  ascending sequence `[0, 1, 2, 3]` (cap at slice length /
  cluster_size, clamped non-overlapping)
- `existing_l2_indices` = synthesised ascending sequence

When the pool is short, slices shrink (down to length zero;
the planner is responsible for returning
`ResizeError::ScratchTooSmall` rather than panicking).

For vhd / vhdx / vmdk the slice layout is simpler — one
512-byte header slice, one 1024-byte dynamic-header slice,
one BAT/GD slice up to 32 KiB. Same shrink-on-short-pool
behaviour.

Invariants (panic = libFuzzer crash):

```rust
fn assert_invariants(plan: &ResizePlan<'_>) {
    let patches = plan.patches();
    assert!(patches.len() <= MAX_RESIZE_PATCHES);

    let total = plan.total_file_size;

    // Per-patch bounds.
    for (i, p) in patches.iter().enumerate() {
        let off = p.byte_offset();
        let len = p.len();
        let end = off.checked_add(len)
            .unwrap_or_else(|| panic!("patch {i}: offset+len overflows"));
        match p {
            // Writes / ZeroFills must land within the final EOF.
            ResizePatch::Write { .. } | ResizePatch::ZeroFill { .. } => {
                assert!(end <= total,
                    "patch {i}: end {end} > total_file_size {total}");
            }
            // Appends must land at or below the final EOF (they
            // define EOF as they fire; the upper bound applies
            // after the planner finishes).
            ResizePatch::Append { .. } => {
                assert!(end <= total,
                    "append {i}: end {end} > total_file_size {total}");
            }
        }
    }

    // No two Write patches overlap. Appends and ZeroFills can
    // legitimately overlap in some planner expressions (e.g. a
    // ZeroFill that the host turns into a hole-punch); we focus
    // on Writes where overlap is unambiguously a bug.
    let mut writes: Vec<(u64, u64)> = patches
        .iter()
        .filter_map(|p| match p {
            ResizePatch::Write { byte_offset, bytes } =>
                Some((*byte_offset, *byte_offset + bytes.len() as u64)),
            _ => None,
        })
        .collect();
    writes.sort_by_key(|(off, _)| *off);
    for w in writes.windows(2) {
        let (_, end_a) = w[0];
        let (off_b, _) = w[1];
        assert!(end_a <= off_b,
            "overlapping Write patches: {:?} and {:?}", w[0], w[1]);
    }
}
```

The re-parse round-trip from `fuzz_create_emitters` does
*not* apply here: resize mutates an existing image, so the
output is the in-memory union of `(existing bytes) +
(planner patches)`. Faithfully reconstructing a complete,
re-parseable image from the fuzzer's synthetic existing-
state bytes is the planner's job *and* the integration
test's job; trying to assert it inside the fuzz target
without a real qcow2/vhd/vhdx parser walk pulls in too much
plumbing for too little signal. Surface 5 of phase 11
(consistency suite) is the cross-check that the planner
+ guest pair actually produces re-parseable files; the fuzz
target's contract is narrower: no panics, no integer
overflows, no overlapping writes, no patch counts above
the bound.

A thread-local reusable scratch (~4 MiB, sized to the
qcow2 worst-case planner working space) avoids per-iteration
allocation. Modelled on `fuzz_create_emitters`'s `SCRATCH`.

### Differential extension: `op_resize` in `differential-fuzz.py`

Structure mirrors `op_create` (line 1002 of the script):

```python
def op_resize(instar_bin, instar_copy, qemu_copy, fmt, timeout, rng):
    """Create the same image twice, resize each via its native
    tool, compare via qemu-img info JSON.

    instar_copy / qemu_copy / fmt are unused — `resize` builds
    its own (target, start, end, opts, prealloc) tuple from the
    picker.
    """
    target, start_size, end_spec, options_list, prealloc = (
        _resize_option_picker(rng))
    apply_shrink = _infer_shrink(start_size, end_spec)

    iter_dir = instar_copy.parent
    ext = {'qcow2': 'qcow2', 'raw': 'raw'}[target]
    inst_path = iter_dir / f'resize-instar.{ext}'
    qemu_path = iter_dir / f'resize-qemu.{ext}'

    # 1. Create the start image twice.
    create_args_base = ['-f', target]
    for opt in options_list:
        create_args_base.extend(['-o', opt])

    i_create_args = create_args_base + [str(inst_path), start_size]
    q_create_args = create_args_base + [str(qemu_path), start_size]

    _, _, i_create_rc = run_instar(
        instar_bin, ['create'], i_create_args, timeout=timeout)
    _, q_create_err, q_create_rc = run_qemu_img(
        ['create'], q_create_args, timeout=timeout)
    if i_create_rc != 0 or q_create_rc != 0:
        return {  # Divergence in create step itself.
            'type': 'resize_create_seed_failure',
            ...
        }

    # 2. Run resize on each.
    resize_args_base = ['-f', target]
    if apply_shrink:
        resize_args_base.append('--shrink')
    if prealloc is not None:
        resize_args_base.extend(['--preallocation', prealloc])

    i_resize_args = resize_args_base + [str(inst_path), end_spec]
    q_resize_args = resize_args_base + [str(qemu_path), end_spec]

    _, i_resize_err, i_resize_rc = run_instar(
        instar_bin, ['resize'], i_resize_args, timeout=timeout)
    _, q_resize_err, q_resize_rc = run_qemu_img(
        ['resize'], q_resize_args, timeout=timeout)

    div = compare_exit_codes(
        i_resize_rc, q_resize_rc, 'resize',
        {'target_format': target,
         'start_size': start_size,
         'end_spec': end_spec,
         'options': options_list,
         'preallocation': prealloc,
         'apply_shrink': apply_shrink,
         'instar_stderr': i_resize_err[:500],
         'qemu_stderr': q_resize_err[:500]},
    )
    if div:
        return div
    if i_resize_rc != 0:
        return None  # both rejected; nothing to compare

    # 3. Compare via qemu-img info on both outputs.
    inst_info_out, _, inst_info_rc = run_qemu_img(
        ['info', '--output=json'], [str(inst_path)], timeout=timeout)
    qemu_info_out, _, qemu_info_rc = run_qemu_img(
        ['info', '--output=json'], [str(qemu_path)], timeout=timeout)
    # ... rest mirrors op_create's parse + normalise + compare logic
```

The `_resize_option_picker(rng)` returns a 5-tuple
`(target, start_size, end_spec, options_list, prealloc)`.
Constraints:

- **target**: `'qcow2'` or `'raw'` only. vmdk/vhd/vhdx are
  excluded because qemu rejects resize on them — the
  differential surface can't compare against a no-op.
- **qcow2 options**: same divergence avoidance as
  `_create_option_picker`:
  - cluster_size from `QCOW2_CLUSTER_SIZES` (already
    defined in the script).
  - extended_l2 with 30% probability (only when
    cluster_size ≥ 16384).
  - lazy_refcounts with 30% probability.
  - **no** `refcount_bits=N` (instar hardcodes 16).
  - **no** `compat=0.10` (instar hardcodes 1.1).
  - **no** `compression_type=zstd` (accept-ignored).
- **size pair**: start_size ∈ `{'1M', '16M', '64M'}`;
  end_spec is one of:
  - absolute (`'4M'` / `'64M'` / `'256M'`) — must differ
    from start
  - additive `+N` where N is one of `'+1M'`, `'+15M'`, `'+63M'`
  - subtractive `-N` (only for qcow2 — instar supports
    qcow2 shrink; raw shrink does too) where the result
    stays positive
- **prealloc**: `None`, `'off'`, `'falloc'`, `'full'` —
  **no** `'metadata'` for qcow2 (the
  PreallocationUnsupported planner gap from
  KNOWN_RESIZE_DIVERGENCES). For raw, exclude
  `'metadata'` (host rejects).
- **shrink-without-flag avoidance**: when end < start, the
  picker auto-applies `--shrink` (the integration tests'
  contract) so the fuzzer doesn't trip the harmless host
  rejection.

`_infer_shrink(start, end)` is the same byte-math helper
used by `tests/test_resize.py:resolve_resize_end_bytes` — port
the function rather than depend on the test module.

Add `'resize'` to the `OPERATIONS` list at line 48 so the
existing random-chain dispatcher picks it up.

## Test surface

For each surface, the smoke before commit is:

**12a smoke** (after the harness lands):
- `cd src/fuzz && cargo fuzz build` — compiles cleanly.
- `cargo fuzz run fuzz_resize_planners -- -max_total_time=60` —
  runs for 60 s, no crashes, corpus grows. Expect 10–100 K
  iterations depending on hardware; expect the planner to
  return errors on most inputs (ScratchTooSmall,
  InvalidNewVirtualSize) which the harness silently
  ignores per the libFuzzer-panics-as-oracle pattern.
- Hand-craft one input that *should* panic if the invariant
  assertions are wrong (e.g. a Write patch at offset
  u64::MAX), verify it's caught — basic sanity that the
  invariants are wired.

**12b smoke** (after the differential extension lands):
- `python3 scripts/differential-fuzz.py
    --instar src/target/release/instar
    --iterations 200
    --seed 42` — 200 iterations against the live system
  qemu-img with a fixed seed. Expect 0 divergences if the
  divergence whitelist is complete; expect a divergence
  report if it isn't (which is the signal to either add to
  the whitelist with a comment, or file a bug).

**12c smoke** (after the CI wiring lands):
- `act` (or local equivalent) on `coverage-fuzz.yml` to
  confirm the new target is in the rotation and the per-
  target duration recalculation works at N=17.
- No change needed to `differential-fuzz.yml` — its
  random-op picker auto-includes the new op.

End-to-end coverage of the resize planner against real
images was already validated in phase 11; phase 12 is the
defensive layer that catches the cases phase 11's curated
matrix doesn't reach.

## Public API delta

None. Phase 12 adds files but doesn't change any public
interface:

- `src/fuzz/fuzz_targets/fuzz_resize_planners.rs` (new)
- `src/fuzz/Cargo.toml` (new dependency + new `[[bin]]`)
- `scripts/differential-fuzz.py` (new function + new
  OPERATIONS entry)
- `.github/workflows/coverage-fuzz.yml` (TARGETS array
  appended, N_TARGETS bumped)
- `README.md` + `ARCHITECTURE.md` (fuzz target count
  updated)

## Open questions

1. **Should the coverage target re-parse the produced bytes?**
   No, for the reason explained in §Algorithmic design — the
   re-parse requires plumbing a synthetic input image
   reconstruction step that's surface-area-heavy without
   strong signal beyond what phase 11's consistency suite
   already provides. *Recommendation*: stop at the plan-
   level invariants; phase 12c's differential harness
   catches the byte-level cases.

2. **Should the coverage target use `arbitrary` for input
   decoding?** `fuzz_create_emitters` decodes by hand
   (byte-indexed unpacking + small `match` blocks).
   `arbitrary` is more ergonomic but adds a dependency
   and obscures the input/coverage relationship. *Recommendation*:
   match the existing style — manual decoding, no
   `arbitrary`.

3. **Should `op_resize` produce a sized image (e.g. 64 MiB)
   instead of empty?** Phase 11's matrix mostly works with
   empty fresh-created images and that's adequate for the
   metadata-only resize paths instar exercises today. Once
   per-format data-region preallocation walks land (master-
   plan Future work), a follow-up phase will add a
   write-pattern-then-resize variant. *Recommendation*:
   empty for v1; queue the populated variant as fuzz
   follow-up.

4. **Should we cover `--preallocation=metadata` for qcow2 in
   the differential picker?** No — `KNOWN_RESIZE_DIVERGENCES`
   marks it as a documented planner gap (deferred from
   phase 2c). Including it would produce a stream of
   `instar rejects / qemu accepts` divergence reports. The
   day the planner lifts the rejection we lift the picker's
   skip too. *Recommendation*: exclude with an inline
   comment pointing at the master-plan Future work entry.

5. **vmdk / vhd / vhdx differential coverage**. qemu-img
   can't resize them; the differential surface can't
   compare against a no-op. Phase 11's consistency suite is
   the only coverage. *Recommendation*: out of scope for
   phase 12; if libyal's `vmdkinfo` / `vhdiinfo` ever gain
   resize support we revisit. Document in
   `_resize_option_picker`'s docstring.

6. **Coverage-fuzz CI duration impact**. The workflow
   recomputes per-target budget from `N_TARGETS`; bumping
   from 16 to 17 changes per-target time from
   ~28 minutes to ~26 minutes. Within the budget envelope
   (~450 min total). No action needed beyond the constant
   bump.

7. **Differential-fuzz CI duration impact**. The script
   picks ops uniformly at random; adding `resize` reduces
   the slice for other ops slightly but doesn't change
   total iteration count or wall-clock. Each `op_resize`
   call does 2 creates + 2 resizes + 2 infos = ~6 qemu/instar
   invocations, comparable to `op_create`'s 2 creates + 2
   infos = 4 invocations. Negligible per-iteration impact.

8. **Should we ship a curated corpus for
   `fuzz_resize_planners`?** A few seed inputs derived
   from the phase 10 baselines would warm the coverage map.
   The existing
   `scripts/extract-fuzz-corpus.py` populates corpora from
   `instar-testdata` images for the parser targets; it
   doesn't have a path for `resize` planner inputs
   (which need a packed `(format_selector, opts, slices)`
   blob, not a raw image). *Recommendation*: ship no
   seed corpus for v1; libFuzzer's coverage feedback
   builds one quickly. Queue an `extract-fuzz-corpus` patch
   as fuzz follow-up if signal needs to improve.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 12a | medium | opus | worktree | Ship `src/fuzz/fuzz_targets/fuzz_resize_planners.rs` per the §Algorithmic design layout. Add `resize = { path = "../crates/resize" }` to `src/fuzz/Cargo.toml`'s `[dependencies]` and a new `[[bin]]` entry for `fuzz_resize_planners`. Implement the invariant assertions per §Algorithmic design exactly — no extra checks beyond what's listed; reviewer scrutiny is on the precision of the invariant set. Run `cd src/fuzz && cargo fuzz build` and `cargo fuzz run fuzz_resize_planners -- -max_total_time=60`; both must complete without crashes. Hand-verify the invariants are wired (e.g. by tweaking the planner to emit a deliberately overlapping pair of Writes in a local checkout, confirming the harness flags it, then reverting). Commit. |
| 12b | medium | sonnet | none | Add `op_resize` + `_resize_option_picker` + `_infer_shrink` to `scripts/differential-fuzz.py`, modelled on `op_create` and `_create_option_picker`. Append `'resize'` to the `OPERATIONS` list at line 48. Update the dispatch `if/elif` chain in `run_iteration` (line ~1119) to call `op_resize`. Run `python3 scripts/differential-fuzz.py --instar src/target/release/instar --iterations 200 --seed 42`; expect 0 divergences. If divergences surface, triage: if the case is in `KNOWN_RESIZE_DIVERGENCES` the picker should have filtered it (fix the picker); if it's genuinely new, add to the divergence whitelist with a comment and commit a follow-up. Commit the script change. |
| 12c | low | sonnet | none | CI + docs wiring. In `.github/workflows/coverage-fuzz.yml`: append `fuzz_resize_planners` to the TARGETS array (~line 227) and bump `N_TARGETS=16` to `N_TARGETS=17` (~line 152). In `README.md` and `ARCHITECTURE.md`: update the fuzz-target count (currently stated as 13, real count was already 16, now 17). Smoke: re-read both workflow files end-to-end for syntax issues; `yamllint` if available. No live CI run needed — the nightly cadence picks up the new target on the next scheduled run. Commit. |
| 12d | low | sonnet | none | Wall-clock smoke + mark phase 12 complete. Run `cd src/fuzz && cargo fuzz run fuzz_resize_planners -- -max_total_time=300` (5 minutes) and confirm no crashes; capture the iteration count. Re-run the 200-iteration differential smoke (`--seed 42`) and confirm clean. Mark phase 12 complete in `docs/plans/PLAN-resize.md`. Commit. |

## Out of scope for phase 12

- Documentation, CHANGELOG, follow-ups (phase 13).
- Per-format data-region preallocation walks (master-plan
  Future work).
- Lifting the qcow2 metadata-preallocation planner gap
  (master-plan Future work).
- Curated seed corpus for `fuzz_resize_planners`
  (open-question item 8; queue as fuzz follow-up if signal
  is poor).
- Populated-image differential coverage (open-question
  item 3; queue as fuzz follow-up).
- vmdk / vhd / vhdx differential coverage (open-question
  item 5; blocked on qemu-img support or a third-party
  resize tool).

## Success criteria for phase 12

- `cargo fuzz build` succeeds with the new target.
- `cargo fuzz run fuzz_resize_planners -- -max_total_time=60`
  completes without crashes.
- `python3 scripts/differential-fuzz.py --iterations 200
  --seed 42` completes with zero divergences.
- `.github/workflows/coverage-fuzz.yml` includes
  `fuzz_resize_planners` and `N_TARGETS=17`.
- `README.md` and `ARCHITECTURE.md` reference the new
  count.
- 5-minute coverage smoke (`max_total_time=300`) clean.
- Differential 200-iter smoke clean a second time.
- Phase 12 row marked Complete in `docs/plans/PLAN-resize.md`.

## Sub-agent guidance

Read these files before starting any step:

- `src/fuzz/fuzz_targets/fuzz_create_emitters.rs` (the
  closest precedent — every structural choice in the new
  target mirrors it).
- `src/fuzz/Cargo.toml` (the `[[bin]]` pattern + dep list).
- `src/crates/resize/src/lib.rs` lines 99–253 (the public
  surface the harness wraps: `ResizePatch`, `ResizePlan`,
  `Preallocation`).
- `src/crates/resize/src/lib.rs` lines 282–500 (the per-
  format `Opts` types the harness builds).
- `scripts/differential-fuzz.py` lines 935–1088
  (`_create_option_picker` + `op_create` — the precedent for
  the differential extension).
- `tests/test_resize.py` lines 34–80
  (`KNOWN_RESIZE_DIVERGENCES` — the dict that drives the
  picker's exclusions).
- `.github/workflows/coverage-fuzz.yml` lines 140–230 (the
  TARGETS array + N_TARGETS constant).
- `docs/plans/PLAN-resize-phase-11-integration-tests.md`
  (phase 11's findings — particularly the rationale for
  vmdk / vhd / vhdx exclusion from any cross-tool surface).

The management session review checklist is the same as
prior phases: per-step `git diff` review; smoke before
commit; report any divergence the differential harness
finds that doesn't have a clear root cause (planner gap
vs. fuzzer-picker bug vs. instar bug) — the user triages
which bucket each finding falls into.
