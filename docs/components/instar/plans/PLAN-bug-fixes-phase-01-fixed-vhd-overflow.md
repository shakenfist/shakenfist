# Phase 1 — Category A: Fixed-VHD `virtual_size` overflow guard

Master plan: [PLAN-bug-fixes.md](/components/instar/plans/PLAN-bug-fixes/)

Closes: #353, #355, #357, #361, #362, #363, #367 (all
`fuzz_create_emitters` invariant-3 panics; all decode to a Fixed
VHD with `virtual_size` ≈ `u64::MAX`).

Planning effort: **medium**. Localised, well-understood, single
code site. The research is already done in this plan.

## Root cause

`plan_vhd` (`src/crates/create/src/lib.rs:741`) has two subformat
branches:

* **Dynamic** (`:760`) computes a `u32` BAT-entry count via
  `u32::try_from(opts.virtual_size.div_ceil(block_size))` and
  returns `CreateError::Overflow` when that conversion fails — so
  an oversize `virtual_size` is already rejected (incidentally,
  via the `u32` ceiling rather than a principled cap).
* **Fixed** (`:845`) writes a single 512-byte footer at
  `byte_offset = opts.virtual_size` (`:860`) and has **no upper
  bound** on `virtual_size`. The resulting plan has
  `total_metadata_bytes = 512` and `minimum_file_size =
  virtual_size + 512`. For `virtual_size` near `u64::MAX`,
  `total_metadata_bytes + minimum_file_size` overflows `u64`,
  tripping invariant 3 in `fuzz_create_emitters.rs:225`.

The only pre-existing `virtual_size` check in `plan_vhd` is the
`== 0` rejection at `:745`. There is no maximum.

## The fix

VHD has a real maximum size. qemu's `block/vpc.c` rejects images
larger than `VHD_MAX_SECTORS` (`0xFF000000` sectors = `4_278_190_080`
sectors = 2040 GiB). Reject `virtual_size` above that same cap with
`CreateError::InvalidVirtualSize`, matching the existing `== 0`
rejection style.

Place the cap **before the subformat split** (right after the
`== 0` check at `:745`-ish) so it covers both Fixed and Dynamic
with one explicit, principled bound — Dynamic's current reliance on
the incidental `u32` BAT overflow then becomes belt-and-braces
rather than the only guard. Verify this does not regress any
existing Dynamic test (the cap is far above any legitimate test
size).

Confirm the exact constant by checking `qemu-img create -f vpc`
boundary behaviour: a `virtual_size` of `0xFF000000 * 512` should
succeed and one sector larger should be rejected. The autofix
attempt-2 on #362 used exactly `0xff00_0000 * 512` — that value is
correct; what it lacked was landing the guard where the Fixed
reproducer actually flows. Verify against the real reproducers, not
only a new unit test.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | In `src/crates/create/src/lib.rs`, add a `virtual_size` upper-bound check to `plan_vhd`. Define a `const VHD_MAX_VIRTUAL_SIZE: u64 = 0xFF00_0000 * 512;` (2040 GiB, matching qemu `vpc.c` `VHD_MAX_SECTORS`) and, immediately after the existing `if opts.virtual_size == 0 { return Err(CreateError::InvalidVirtualSize); }` at the top of `plan_vhd` (~line 745), add `if opts.virtual_size > VHD_MAX_VIRTUAL_SIZE { return Err(CreateError::InvalidVirtualSize); }`. Placing it before the subformat split means it covers both Fixed and Dynamic. Do not change the Dynamic BAT-overflow logic. Confirm the constant by checking that `qemu-img create -f vpc t.vhd <0xFF000000*512 bytes>` succeeds and one sector larger is rejected. |
| 1b | medium | sonnet | none | Add a regression unit test in the `vhd_plan_tests` module of the same file. Pin: (i) `virtual_size = VHD_MAX_VIRTUAL_SIZE` is accepted for both `Fixed` and `Dynamic`; (ii) `virtual_size = VHD_MAX_VIRTUAL_SIZE + 512` is rejected with `CreateError::InvalidVirtualSize` for both; (iii) the exact fuzz reproducer values (e.g. `0xfffffffffffffd80` from #367) are rejected. Use the existing `default_fixed(...)` / `default_dynamic(...)` helpers already present in that test module. |
| 1c | medium | sonnet | none | Reconstruct each of the 7 reproducers from the Base64 blob in the issue bodies into `src/fuzz/artifacts/fuzz_create_emitters/`, then verify `cd src/fuzz && cargo fuzz run fuzz_create_emitters artifacts/fuzz_create_emitters/<file>` no longer panics for each. Then run `cargo fuzz run fuzz_create_emitters -- -max_total_time=600` and confirm no new crash. (Fuzz build is Docker-wrapped per the Rust-in-Docker host preference.) |

## Verification

- [ ] `make instar` builds, `make lint` clean.
- [ ] `make check-binary-sizes` passes (no guest code changed, but confirm).
- [ ] `make test-rust` passes including the new regression test.
- [ ] All 7 reproducers no longer crash; 10-minute campaign clean.
- [ ] `pre-commit run --all-files` passes.

## Commit

One commit. Message body should explain that the Fixed-VHD branch
lacked the `virtual_size` upper bound the Dynamic branch got for
free via its `u32` BAT count, name the 2040 GiB / `0xFF000000`-sector
cap and its qemu `vpc.c` provenance, and close all seven issues:

```
Closes #353
Closes #355
Closes #357
Closes #361
Closes #362
Closes #363
Closes #367
```
