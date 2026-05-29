# Phase 1 — `plan_vmdk` capacity panic (category A1)

Parent plan: [PLAN-fuzzing-bugs.md](/components/instar/plans/PLAN-fuzzing-bugs/)

## Goal

Replace the panicking arithmetic in `plan_vmdk` with checked
operations that surface a typed `CreateError`, so the seven
queued reproducers (and any future inputs that drive the same
math) stop tripping libfuzzer's panic oracle.

**Closes:** #339, #331, #328, #322, #318, #314, #309
(of which #328, #322, #318, #314, #309 are `autofix-failed`).

## Planning effort

Medium. The panic site is a single function and the fix shape
is well-understood. No new APIs, no new error variants needed
beyond what `CreateError` already carries
(`InvalidGrainSize`, `Overflow`).

## Investigation

The panic signature in every issue points at
`src/crates/create/src/lib.rs:526:26`. Line 526 reads:

```rust
let capacity_bytes = opts.virtual_size.div_ceil(grain_size_bytes) * grain_size_bytes;
```

Two distinct panic conditions reach this expression:

1. **Divide by zero.** `u64::div_ceil` panics if the divisor is
   zero. `grain_size_bytes` is `opts.grain_size as u64`. The
   harness derives `grain_size` from fuzz input (see
   `fuzz_create_emitters.rs` — the `rcb_sel` byte selects from
   a table that, for VMDK, includes `0`). `plan_vmdk` already
   rejects bad `grain_size` values elsewhere (`InvalidGrainSize`)
   but does so *after* this line.
2. **Multiplication overflow.** For large `virtual_size` with a
   small but nonzero `grain_size`, `div_ceil` returns a value
   close to `u64::MAX / grain_size_bytes`; multiplying it back
   by `grain_size_bytes` overflows the implicit `*` (debug:
   panic; release: wrap, which then poisons downstream
   sector math).

The fuzz harness's documented oracle is *panic*. The structural
invariants on the returned `MetadataPlan` are already asserted
on a successful return. The fix is therefore to convert these
two failure modes into `CreateError` returns the harness will
silently accept.

## Implementation

Two changes in `src/crates/create/src/lib.rs`, inside the
`plan_vmdk` function:

1. **Hoist the grain-size validation.** Move the existing
   `InvalidGrainSize` check (it lives lower in `plan_vmdk` —
   look for the existing `CreateError::InvalidGrainSize`
   return) above line 526, before any arithmetic uses
   `grain_size_bytes`. Or add an explicit
   `if grain_size_bytes == 0 { return Err(InvalidGrainSize); }`
   guard immediately above the `div_ceil` call. Hoisting the
   existing check is cleaner; verify no other code between
   the original check site and line 526 depends on values
   computed in between.
2. **Use checked arithmetic.** Replace
   ```rust
   let capacity_bytes = opts.virtual_size.div_ceil(grain_size_bytes) * grain_size_bytes;
   ```
   with
   ```rust
   let capacity_bytes = opts
       .virtual_size
       .div_ceil(grain_size_bytes)
       .checked_mul(grain_size_bytes)
       .ok_or(CreateError::Overflow)?;
   ```

Then audit the next ~30 lines (the GD-entry / total-sector /
total-bytes math through ~line 555) for any other `*`,
`<<`, or `+` on `u64` values driven by `virtual_size` or
`grain_size_bytes`. Convert each to `checked_*` and route
overflows to `CreateError::Overflow`. The
`u32::try_from(capacity_sectors.div_ceil(sectors_per_gt))`
already uses fallible conversion — that pattern is the
target.

## Verification

1. Re-run each filed reproducer locally:
   ```
   cd src/fuzz
   for h in 99b62b300d3b6f80a3ed9218892c1e18f0a4ffe8 \
            362d4423d59f985ed479b2b73a1421532bcef77c \
            cbb039b77e63b16dd7f45abf624a64887590ba4d \
            5b9efaa076ef5d2b987bc8a93a1b0c0f2b981cb8 \
            9a5948aff0640fda2d150bfcf0419a1a1bc7d0d2 \
            675e99240af53ac1b9a32351430334d986218c36 \
            f12af4e28341abbcb103f00ec4c79741013c5cab; do
       cargo fuzz run fuzz_create_emitters \
           artifacts/fuzz_create_emitters/crash-$h || echo "STILL CRASHES: $h"
   done
   ```
   (The exact hashes are listed in each issue body; the script
   above is the union of the seven.)
2. Run a fresh short campaign:
   `cargo fuzz run fuzz_create_emitters -- -max_total_time=600`.
   Expect zero crashes.
3. `cargo test -p create` covers `plan_vmdk` unit tests — they
   should still pass.

## Steps

| Step | Effort | Model | Isolation | Brief |
|------|--------|-------|-----------|-------|
| 1a | medium | sonnet | none | In `src/crates/create/src/lib.rs`, inside `plan_vmdk`: hoist the existing `CreateError::InvalidGrainSize` guard so it precedes the `capacity_bytes` calculation at line 526, then convert the `div_ceil(...) * grain_size_bytes` expression and the surrounding capacity / total-sector / total-bytes arithmetic (~lines 521-560) from raw `*`/`+`/`<<` to `checked_*`, routing failures to `CreateError::Overflow`. Do not introduce new `CreateError` variants. |
| 1b | low | sonnet | none | Add unit tests in the same file (or `create::tests`) for `plan_vmdk` covering: `grain_size = 0` returns `InvalidGrainSize`; `virtual_size = u64::MAX` with smallest valid `grain_size` returns `Overflow`. Use the existing test scaffolding. |
| 1c | low | sonnet | none | Verify all seven reproducers (#339, #331, #328, #322, #318, #314, #309) no longer crash by running the bash loop in the Verification section. Then run the 10-minute campaign. |
| 1d | low | sonnet | none | Close the seven issues with `gh issue close <n> -c "Fixed in <sha>. Root cause: panic in plan_vmdk capacity arithmetic; see PLAN-fuzzing-bugs-phase-01-create-emitters.md."`. |

## Commit shape

One commit for steps 1a + 1b ("create: checked arithmetic in
plan_vmdk capacity calculation"). Step 1c is verification only.
Step 1d is post-merge GitHub housekeeping.
