# Phase 3 — measure calculator overflow (category A3)

Parent plan: [PLAN-fuzzing-bugs.md](/components/instar/plans/PLAN-fuzzing-bugs/)

## Goal

Make each `measure_<format>` calculator surface
`MeasureError::Overflow` (instead of returning a `MeasureOutput`
whose `required + fully_allocated` overflows `u64`) so the
fifteen queued reproducers stop tripping the harness assert.

**Closes:** #337, #333, #329, #327, #320, #316, #312, #307,
#305, #303, #296, #294, #291, #290, #289 (of which #333, #305,
#290 are `autofix-failed`).

## Planning effort

Medium. There are four calculators (`measure_qcow2`,
`measure_vhd`, `measure_vhdx`, `measure_vmdk`) in
`src/crates/measure/src/` — the bug shape is the same in each
(unchecked sums of cluster/block-table overhead against an
adversarial input virtual size). Need to read each calculator
to find the unguarded sums; mechanical from there.

## Investigation

The harness assert (`fuzz_measure_calc.rs:144`):

```rust
assert!(
    m.required.checked_add(m.fully_allocated).is_some(),
    "required + fully_allocated overflows u64"
);
```

is fired when the calculator returns `(required, fully_allocated)`
whose individual values are valid `u64`s but whose sum overflows.
The condition is itself an invariant the harness chose to assert
because `MeasureOutput`'s consumers (e.g. the JSON printer)
add the two fields together when reporting to qemu-img-parity
output.

The fix lives inside each `measure_*` function. There are two
common patterns to audit:

1. **`required` derived from `virtual_size + header_overhead`**
   where `virtual_size` is already near `u64::MAX`. Replace
   `a + b` with `a.checked_add(b).ok_or(MeasureError::Overflow)?`.
2. **`fully_allocated` derived from
   `clusters * cluster_size + metadata`** where the multiply
   overflows. Replace with
   `a.checked_mul(b).and_then(|p| p.checked_add(m))
        .ok_or(MeasureError::Overflow)?`.

After the per-field guards are in, add a *final* guard before
returning `MeasureOutput`:

```rust
required.checked_add(fully_allocated).ok_or(MeasureError::Overflow)?;
```

This makes the invariant the harness is asserting an explicit
contract of the calculator function, not an implicit
side-effect of the per-field checks.

## Implementation

In `src/crates/measure/src/` (one calculator per file or all in
`lib.rs` depending on the layout — grep for `pub fn measure_`):

1. For each of `measure_qcow2`, `measure_vhd`, `measure_vhdx`,
   `measure_vmdk`:
   * Walk every arithmetic operation that contributes to
     `required` or `fully_allocated`.
   * Convert raw `*`/`+`/`<<` to `checked_*` and route to
     `MeasureError::Overflow`.
   * Add the final `required.checked_add(fully_allocated)`
     guard.
2. Update or add unit tests per calculator that exercise the
   overflow path with `virtual_size = u64::MAX`.

The fuzz harness covers all four target selectors
(`target_sel = data[0] % 4` in the harness header). The 15
issues are spread across the four — re-examine the reproducer
inputs after the fix to confirm at least one input per
calculator is exercised in the regression set.

## Verification

1. Re-run each filed reproducer:
   ```
   cd src/fuzz
   cargo fuzz run fuzz_measure_calc artifacts/fuzz_measure_calc/crash-<hash>
   ```
   for each of the fifteen hashes. None should crash.
2. Run a 10-minute campaign:
   `cargo fuzz run fuzz_measure_calc -- -max_total_time=600`.
3. `make test-rust` — in particular the `measure` crate tests
   and the `measure` integration tests.
4. `make test-integration` with the measure subset — confirm
   the cross-version baselines in
   `tests/baselines/measure/` are unchanged (none of these
   adversarial sizes appear in real fixtures).

## Steps

| Step | Effort | Model | Isolation | Brief |
|------|--------|-------|-----------|-------|
| 3a | medium | sonnet | none | In `src/crates/measure/src/`, convert all unchecked arithmetic in `measure_qcow2`, `measure_vhd`, `measure_vhdx`, `measure_vmdk` to `checked_*` operations routed to `MeasureError::Overflow`. Add a final `required.checked_add(fully_allocated).ok_or(Overflow)?` guard before each function returns its `MeasureOutput`. Keep `no_std` compatibility. |
| 3b | low | sonnet | none | Add per-calculator unit tests using `virtual_size = u64::MAX` and (where applicable) extreme `cluster_size` / `grain_size` / `block_size` values. |
| 3c | low | sonnet | none | Verify the fifteen reproducers pass and run a 10-minute fuzz campaign. |
| 3d | low | sonnet | none | Close the fifteen issues with `gh issue close <n> -c "Fixed in <sha>. Root cause: measure_* calculator returned outputs whose required + fully_allocated overflowed u64; see PLAN-fuzzing-bugs-phase-03-measure-calc.md."`. |

## Commit shape

One commit for steps 3a + 3b ("measure: guard calculator
arithmetic against u64 overflow"). Optionally split into one
commit per calculator if the diff is large — each calculator
edit is self-contained.
