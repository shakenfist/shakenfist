# Phase 2 — Category B: VHDX resize sequence-number overflow

Master plan: [PLAN-bug-fixes.md](/components/instar/plans/PLAN-bug-fixes/)

Closes: #354, #360 (both `fuzz_resize_planners` panics at
`src/crates/resize/src/vhdx.rs:248:34`).

Planning effort: **medium**. Localised, well-understood, single
code site with four call points.

## Root cause

The VHDX resize planner increments the on-disk header sequence
number when it writes the two updated header copies. The value
comes straight from the parsed source header — in the fuzzer it is
set verbatim from 8 input bytes (`fuzz_resize_planners.rs:292`,
`current_sequence_number: u64::from_le_bytes(...)`), so it is fully
attacker-controlled.

There are **four** unchecked increments in `vhdx.rs`:

* grow planner: `:162` `... + 1`, `:163` `... + 2`
* shrink planner: `:247` `... + 1`, `:248` `... + 2`

When `current_sequence_number` is at or within 2 of `u64::MAX`, the
`+ 2` overflows and panics in debug builds. The reproducers happen
to land on the shrink-path `:248` (`+ 2`), but the grow path has the
identical defect — the fix must cover all four.

## The fix

A real VHDX never approaches `u64::MAX` for its sequence number
(it is incremented once per header write over the life of the
image); a header that claims such a value is corrupt. Reject it
once, up-front, rather than scattering `checked_add` across four
sites.

Recommended shape: at the top of each planner (or in a shared
helper both call), if `opts.current_sequence_number > u64::MAX - 2`
return `ResizeError::Overflow`. Then the `+ 1` / `+ 2` sites are
provably safe. Confirm `ResizeError::Overflow` exists (it is used
elsewhere in this file, e.g. the `new_bat_length` `try_into` at
`:250-252`); if a more specific variant fits the existing error
taxonomy better, prefer it, but `Overflow` is the natural match.

Before finalising, check whether `build_header` itself takes the
incremented value or does its own arithmetic, and whether any
non-fuzz caller (the guest resize op, the host CLI) passes a
sequence number that could legitimately be large — it should not,
but confirm by reading the call sites.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | medium | sonnet | none | In `src/crates/resize/src/vhdx.rs`, guard the sequence-number increment. Both the grow planner (around line 162) and the shrink planner (around line 247) do `build_header(buf, opts.current_sequence_number + 1)` / `+ 2`. Add, at the top of each planner before any header is built, `if opts.current_sequence_number > u64::MAX - 2 { return Err(ResizeError::Overflow); }` (confirm `ResizeError::Overflow` is the right variant — it is already used later in the same function for the BAT-length `try_into`). If both planners share a natural entry point or a small helper, place the check once there instead of duplicating. Do not change the increment values themselves. |
| 2b | medium | sonnet | none | Add a regression unit test in `vhdx.rs` (or the resize crate's test module) that calls the shrink planner with `current_sequence_number = u64::MAX` and asserts `Err(ResizeError::Overflow)`, and likewise for the grow planner. Also assert that a normal small sequence number (e.g. 1) still succeeds, so the guard is not over-broad. |
| 2c | medium | sonnet | none | Reconstruct the #354 and #360 reproducers from the Base64 blobs into `src/fuzz/artifacts/fuzz_resize_planners/` and verify `cd src/fuzz && cargo fuzz run fuzz_resize_planners artifacts/fuzz_resize_planners/<file>` no longer panics. Then run `cargo fuzz run fuzz_resize_planners -- -max_total_time=600` and confirm no new crash. (Docker-wrapped Rust build.) |

## Verification

- [ ] `make instar` builds, `make lint` clean.
- [ ] `make check-binary-sizes` passes.
- [ ] `make test-rust` passes including the new regression tests.
- [ ] Both reproducers no longer crash; 10-minute campaign clean.
- [ ] `pre-commit run --all-files` passes.

## Commit

One commit. Body should explain that the VHDX resize planner
incremented the attacker-controlled parsed sequence number without
bounds (four call sites across grow and shrink), that a header
near `u64::MAX` is corrupt and is now rejected once up-front, and:

```
Closes #354
Closes #360
```
