# PLAN-dd phase 08: Coverage-guided fuzzing

Master plan: [PLAN-dd.md](/components/instar/plans/PLAN-dd/)
Previous phase: [PLAN-dd-phase-07-baselines.md](/components/instar/plans/PLAN-dd-phase-07-baselines/)

## Status: Complete (8a 1ddb0c5, 8b 46f8a71)

> **Outcome.** 8a: extracted `compute_dd_window` + `DdWindow` into a
> new `no_std` `crates/dd` library crate (8 tests moved, run_dd
> rewired). 8b: added `fuzz_dd_window`, `fuzz_chs_rounded_size`,
> `fuzz_dd_read` (read primitives via mock CallTable); registered in
> `src/fuzz/Cargo.toml` + the `coverage-fuzz.yml` target list (count
> reconciled to the real 26 targets). `make fuzz-build` compiles
> all; each smoke-ran 30s with no crash (~40M runs for the math
> targets). The fuzz oracle is live — three over-strong first-draft
> invariants crashed and were corrected (CHS 127.5 GiB ceiling cap;
> `compute_vhd_geometry` floors so exact round-trip isn't universal;
> zero-length reads out of domain) — all inherent behaviour, no code
> bugs. `fuzz_dd_operands` intentionally omitted (CLI parsing isn't
> fuzzed for any command).

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Flag any uncertainty
explicitly rather than guessing.

## Mission

Add coverage-guided (libFuzzer / cargo-fuzz) targets for the
dd-specific pure logic, following the **established pattern**: each
command's pure planner/format logic lives in a `src/crates/<cmd>/`
library crate and is fuzzed from `src/fuzz/` (e.g.
`fuzz_resize_planners` → `crates/resize`, `fuzz_create_emitters` →
`crates/create`). The fuzz crate (`src/fuzz`, `instar-fuzz`) depends
on the **library** crates, not the `vmm` binary.

## Design

### What is fuzzable today, and the one gap

- **`vhd::chs_rounded_size`** — already in the `vhd` library crate
  (a fuzz dep). Directly fuzzable. Branchy CHS arithmetic (the
  highest-value new dd target).
- **`qcow2::read_raw_sectors` / `read_cluster_sectors`** — already
  in the `qcow2` library crate. Fuzzable with a mock `CallTable`
  (the phase-5 unit tests built exactly such a mock). Byte-range
  math worth fuzzing for OOB/panic.
- **`compute_dd_window`** — the dd window math, currently in the
  `vmm` **binary** (`run_dd`'s neighbourhood), so the fuzz crate
  **cannot reach it**. This is the gap: dd's pure planner-equivalent
  lives in the binary instead of a crate, unlike every other
  command. Phase 8 fixes that by extracting it to a library crate.

### Extraction: `src/crates/dd/`

Create a new `src/crates/dd/` library crate (mirroring
`crates/create|resize|amend`) and move `compute_dd_window` + the
`DdWindow` struct into it. `run_dd` in `vmm` imports
`dd::compute_dd_window`; the phase-5 window unit tests move to (or
are added in) the `dd` crate. Register `crates/dd` in the
`[workspace] members` of `src/Cargo.toml` and add it as a
`src/fuzz/Cargo.toml` dependency. This is a small, low-risk
extraction (one pure fn + one struct, no I/O, no `parse_qemu_img_size`
dependency — see scope note).

### Scope note: `fuzz_dd_operands` is intentionally NOT added

The master plan sketched `fuzz_dd_operands` (over `parse_dd_operands`)
alongside `fuzz_dd_window`. Dropping it, with justification:
- `parse_dd_operands` is **CLI operand parsing**. No instar command
  fuzzes its host-side CLI parser — the fuzz targets cover format /
  planner logic in crates, not clap/argument parsing. Fuzzing dd's
  parser would be inconsistent.
- It depends on `parse_qemu_img_size` (also in the `vmm` binary,
  shared with resize/create arg parsing). Fuzzing it would force
  relocating that parser to a library crate — a wider refactor for
  low value.
- `parse_dd_operands` is already covered by the phase-5
  `dd_operand_tests` (rejection/clamp/suffix cases).
`compute_dd_window` (the *planner-equivalent* — the dd analog of
the resize planners) IS fuzzed, matching how sibling planners are
fuzzed. If the operator wants full master-plan fidelity, the
`fuzz_dd_operands` target + the `parse_qemu_img_size`→`shared` move
can be added as a follow-up; flag it rather than silently skip.

### The three targets

1. **`fuzz_dd_window`** (over `dd::compute_dd_window`). Decode the
   fuzzer bytes into `(virtual_size, bs, count_opt, skip)` (e.g. a
   fixed-size header → four u64s + a flag for `count` present).
   Call `compute_dd_window`. Assert (panic is libFuzzer's oracle;
   add cheap invariant asserts): no panic (saturating arithmetic);
   `out_vsize <= end`; `end == min(virtual_size, count*bs)` or
   `virtual_size` when count absent; `start == skip*bs` (saturating);
   `out_vsize == end.saturating_sub(start)`. Guard `bs` to the
   valid `1..=INT_MAX` range the parser enforces, OR fuzz the raw
   inputs and accept any saturating result (document which).
2. **`fuzz_chs_rounded_size`** (over `vhd::chs_rounded_size`).
   Decode a u64 size. Assert: no panic/overflow; `result >= size`
   for `size > 0`; `result == 0` for `size == 0`; CHS
   self-consistency `compute_vhd_geometry(result)` product × 512 ==
   `result` **except** in the very-large (spt=255) region where CHS
   cannot represent the size exactly (mirror the phase-5 unit-test
   exclusion).
3. **`fuzz_dd_read`** (over `qcow2::read_raw_sectors` and
   `read_cluster_sectors`). Build a mock `CallTable` serving a
   bounded in-memory device with a position-dependent pattern
   (adapt the phase-5 unit-test mock — note fuzz targets can't use
   `#[cfg(test)]` code, so the mock lives in the target or a shared
   non-test helper). Decode the fuzzer bytes into
   `(virtual_offset, length, sector_size, capacity)` within sane
   bounds. Call the primitive into a buffer of the right size.
   Assert: no panic/OOB; returned in-range bytes match the pattern;
   bytes past capacity are zero. (Find an existing CallTable-using
   fuzz target as the harness template — the `src/fuzz/Cargo.toml`
   header distinguishes "buffer-based (no CallTable)" targets, so
   some targets already build a CallTable.)

### Registration + CI

- `src/fuzz/Cargo.toml`: add `dd = { path = "../crates/dd" }` to
  deps and a `[[bin]]` entry for each new target.
- `.github/workflows/coverage-fuzz.yml`: the target list is
  **hardcoded** (the explicit list around line 209+, and a "default
  22" count comment). Add the three new target names to that list
  and bump the count (22 → 25). Check `differential-fuzz.yml` and
  `fuzz-autofix.yml` for any similar hardcoded enumerations and
  update consistently.
- No committed seed corpus is required for the targets to run;
  nightly corpus accrual lands in the testdata repo (out of scope
  to seed here — note it).

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 8a | high | opus | none | Extract `compute_dd_window` + `DdWindow` from `src/vmm/src/main.rs` into a new `src/crates/dd/` library crate (Cargo.toml mirroring `crates/amend`/`crates/resize`; `no_std` is NOT required for host-only logic — check whether the fuzz-linked crates are `no_std` and match the convention, but compute_dd_window is plain `u64` math so either works). Re-export the fn + struct; update `run_dd` in `vmm` to `use dd::{compute_dd_window, DdWindow}`; move the `compute_dd_window` unit tests from `vmm`'s `dd_operand_tests` into the `dd` crate (leave `parse_dd_operands` and its tests in `vmm`). Add `crates/dd` to `[workspace] members` in `src/Cargo.toml`. Verify `make instar` + `make test-rust` + `make lint` are green and the moved tests run in the `dd` crate. Do NOT touch `parse_dd_operands` / `parse_qemu_img_size`. |
| 8b | high | opus | none | Add three fuzz targets in `src/fuzz/fuzz_targets/`: `fuzz_dd_window.rs` (over `dd::compute_dd_window` — invariants per Design §1), `fuzz_chs_rounded_size.rs` (over `vhd::chs_rounded_size` — §2), `fuzz_dd_read.rs` (over `qcow2::read_raw_sectors`/`read_cluster_sectors` via a mock `CallTable` — §3; study an existing CallTable-using target and the phase-5 mock). Add `dd = { path = "../crates/dd" }` to `src/fuzz/Cargo.toml` deps + a `[[bin]]` block per target. Register the three names in `.github/workflows/coverage-fuzz.yml`'s hardcoded list and bump the count; check/adjust `differential-fuzz.yml` + `fuzz-autofix.yml` if they enumerate targets. SMOKE-RUN each target briefly inside the fuzz toolchain (e.g. `cargo +nightly fuzz run <target> -- -runs=100000 -max_total_time=30` or the project's documented invocation — read AGENTS.md / the workflow for how fuzzing is run, likely in a container) to confirm it builds and finds no immediate crash; report the run output. If a target crashes, that's a real finding — minimise and report, do not weaken the invariant. |

Per the master plan / `PLAN-TEMPLATE.md`, sub-agents implement and
the management session reviews before committing. Suggested
commits: 8a the `crates/dd` extraction, 8b the fuzz targets + CI
registration. The extraction (8a) must land green before 8b builds
on it.

## Verification

- [ ] `crates/dd` builds, is in the workspace, and `run_dd` uses it;
      `make instar` / `make test-rust` / `make lint` green; the
      moved `compute_dd_window` tests run in the `dd` crate.
- [ ] `cargo fuzz build` succeeds with the three new targets; each
      smoke-runs without an immediate crash.
- [ ] Invariant asserts are real (a deliberately-wrong invariant
      would fail the smoke run) — not just `let _ = f(x)`.
- [ ] The three targets are registered in
      `coverage-fuzz.yml` (and any other enumerating workflow) and
      the count is updated.
- [ ] `make check-binary-sizes` unaffected (fuzz + dd crate are not
      guest binaries; confirm `crates/dd` isn't pulled into a guest
      op that would bloat it).
- [ ] `pre-commit run --all-files` passes.
- [ ] Commit messages follow conventions (model/context/effort).
- [ ] `fuzz_dd_operands` deliberately omitted (scope note) — recorded
      in the commit message / Future work, not silently dropped.

## Hand-off

Remaining phases: 9 differential fuzzing vs `qemu-img dd` (random
`bs`/`count`/`skip`/`-O` invocations cross-checked against the real
binary — add `dd` to `scripts/differential-fuzz.py`; this is also
where the `count=0 -O vhdx` limitation should be resolved or
explicitly excluded), 10 docs (`docs/dd.md` + README/ARCHITECTURE/
AGENTS/CHANGELOG + the `index.md` Complete flip + the deferred
phase-7 ARCHITECTURE/CHANGELOG baseline notes). The
[[dd-qemu-img-parity-contract]] memory records the verified rules.
