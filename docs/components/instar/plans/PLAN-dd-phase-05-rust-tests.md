# PLAN-dd phase 05: Rust unit tests

Master plan: [PLAN-dd.md](/components/instar/plans/PLAN-dd/)
Previous phase: [PLAN-dd-phase-04-guest-formats.md](/components/instar/plans/PLAN-dd-phase-04-guest-formats/)

## Status: Complete (44faa76)

> **Outcome.** 26 hermetic unit tests added (vhd 3, qcow2 9, vmm
> 14); `make test-rust` 0-fail. The qcow2 read-primitive tests were
> confirmed non-tautological (mutating the copy flips five to
> FAILED). One nuance: the `chs_rounded_size` CHS self-consistency
> property holds for all but the very large sizes (≳ the spt=255
> CHS region), where CHS cannot represent the size exactly — the
> qemu-match table still covers those, matching qemu's own
> behaviour.

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

Add Rust **unit** tests for the pure / mockable logic introduced
across phases 1–4 that is not yet unit-covered, so that the
behaviour verified empirically against `qemu-img dd` is also
locked down by fast, hermetic tests that run under `make test-rust`
(no KVM, no testdata). Integration tests (phases 3/4) already cover
the end-to-end guest behaviour; this phase fills the unit gaps —
chiefly the two pure functions that are currently **only**
integration-verified.

## What's already covered (don't duplicate)

- `parse_dd_operands` and `compute_dd_window` — 22 tests in
  `mod dd_operand_tests` (`src/vmm/src/main.rs` ≈ 15845), added in
  phase 2 (operand parsing, size suffixes, count clamp, skip math,
  empty/overflow). Extend only for genuine gaps.

## Gaps to fill (this phase)

### 1. `vhd::chs_rounded_size` — highest value

New in phase 4 (`src/crates/vhd/src/lib.rs` ≈ 317), it replicates
qemu-img's CHS round-up for VHD output virtual size. It is tricky
(sectors-per-track thresholds 17/31/63, head counts, a one-cylinder
minimum, a max cap) and is currently verified **only** through dd
integration tests. The `vhd` crate already has
`#[cfg(test)] mod tests` (≈ 1078) with `compute_vhd_geometry`
tests to mirror.

Add tests asserting it against these **qemu-img 10.0.8-verified**
pairs (`qemu-img create -f vpc <size>` then read `virtual-size`):

| input | expected |
|-------|----------|
| 0 | 0 (empty window — special-cased) |
| 512 | 34816 |
| 1000 | 34816 |
| 3000 | 34816 |
| 34816 | 34816 (boundary, already CHS-aligned) |
| 34817 | 69632 (just over) |
| 65536 | 69632 |
| 131072 | 139264 |
| 1048576 | 1079296 |
| 1073741824 (1 GiB) | 1073995776 |
| 10737418240 (10 GiB) | 10737893376 |

Plus two property tests:
- **CHS self-consistency:** for each input `s`, let
  `r = chs_rounded_size(s)`; `compute_vhd_geometry(r)` returns
  `(c, h, spt)` with `c as u64 * h as u64 * spt as u64 * 512 == r`
  (the doc comment's invariant — the footer geometry matches the
  declared size). Test across the table inputs.
- **Rounds up:** `chs_rounded_size(s) >= s` for `s > 0`.

### 2. Byte-accurate read primitives — regression protection

Phase 3 rewrote `read_raw_sectors` (`src/crates/qcow2/src/lib.rs`
≈ 4907) and `read_cluster_sectors` (≈ 2635) to read arbitrary
sub-sector byte ranges, with a sector-aligned **fast path** that
must stay byte-identical. This is a shared primitive used by every
reader, so it deserves hermetic unit tests. The qcow2 crate already
has a mock call-table helper — `make_streaming_call_table`
(≈ 4679) — and 99 existing tests; study how they feed device bytes
to `read_input_sector` and mirror that.

Build (or reuse) a mock `CallTable` whose `read_input_sector`
serves a deterministic, position-dependent pattern from an
in-memory device of known capacity (e.g. byte at absolute offset
`o` is `(o % 251)`), then assert:
- **Aligned fast path** (offset and length both sector multiples):
  result equals the reference slice — and equals what the original
  whole-sector loop would produce (the refactor preserved it).
- **Sub-sector start** (offset partway into a sector): result ==
  `pattern[offset .. offset+len]` exactly.
- **Sub-sector length** (length not a sector multiple): the tail
  bytes are correct (no dropped tail, which was the phase-3 bug).
- **Span** (range covering a partial head sector, full interior
  sectors, and a partial tail sector at once).
- **Past capacity:** reads at/after the device end zero-fill the
  remainder (both fast and byte-accurate paths).
- For `read_cluster_sectors`, the analogous cases including the
  old sub-sector-cluster case (`cluster_size < sector_size`) and a
  non-zero `host_offset` into a multi-sector cluster (the latent
  bug phase 3 fixed).

Pick a small sector size (e.g. 512) and small device so cases are
easy to reason about. These tests are `no_std`-crate tests but run
on the host under `cargo test` like the existing qcow2 tests.

### 3. `parse_output_format` + `compute_output_capacity`

Both extracted in phase 2 (`src/vmm/src/main.rs` ≈ 8679 / 8696) and
currently untested. Add to `mod dd_operand_tests` (or a sibling
module):
- `parse_output_format`: `"raw"`→1, `"qcow2"`→2, `"vmdk"`→3,
  `"vpc"`→5, `"vhdx"`→6, and an unknown string → `Err`.
- `compute_output_capacity`: raw returns the vsize unchanged;
  qcow2/vmdk/vhd add their documented headroom; vhdx rounds up as
  documented. Assert the relationships (e.g. raw == vsize;
  structured > vsize) rather than hard-coding fragile exact byte
  counts where the formula is "vsize + 1% + 10 MB" — but DO assert
  the exact VHDX rounding if it's a clean formula.

### Optional (only if quick): extend `dd_operand_tests`

- The `b`=512 suffix: `parse_qemu_img_size` treats a trailing `b`
  as 512 (an instar-ism vs qemu_strtosz). Add a test documenting
  the current behaviour so any future change is deliberate.
- `bs` at the exact `INT_MAX` boundary (2147483647 accepted,
  2147483648 rejected).

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | medium | sonnet | none | In `src/crates/vhd/src/lib.rs` `#[cfg(test)] mod tests` (≈ 1078), add tests for `chs_rounded_size` against the qemu-verified pair table in this plan (incl. 0→0), plus the CHS self-consistency property (`compute_vhd_geometry(chs_rounded_size(s))` product × 512 == `chs_rounded_size(s)`) and `chs_rounded_size(s) >= s` for `s>0`. Mirror the existing `compute_vhd_geometry` test style. Run `cargo test -p vhd` (note any feature gating) and `make test-rust`; report results. |
| 5b | medium | sonnet | none | In `src/crates/qcow2/src/lib.rs` `#[cfg(test)] mod tests` (≈ 3027), add hermetic unit tests for `read_raw_sectors` and `read_cluster_sectors` byte-accuracy using a mock `CallTable` that serves a position-dependent pattern from an in-memory device (study `make_streaming_call_table` ≈ 4679 and existing read tests; build a minimal mock if that helper doesn't fit). Cover: aligned fast path, sub-sector start, sub-sector length (tail), full span (head+interior+tail), past-capacity zero-fill; for `read_cluster_sectors` also `cluster_size < sector_size` and non-zero `host_offset` into a multi-sector cluster. Assert results equal the reference pattern slice. If the mock proves fiddly, escalate to opus. Run `cargo test -p qcow2` (note: qcow2 tests may need a feature — `cargo test -p qcow2 --features create` per the create module) and `make test-rust`; report results. |
| 5c | low | sonnet | none | In `src/vmm/src/main.rs` (in `mod dd_operand_tests` or a sibling test module), add tests for `parse_output_format` (each format string → code, unknown → Err) and `compute_output_capacity` (raw == vsize; structured > vsize; exact VHDX rounding if a clean formula). Optionally add the `b`-suffix and INT_MAX-boundary `bs` tests. Run `make test-rust`; report. |

These are additive, test-only, hermetic (no KVM/testdata), and
touch three independent files, so the steps are independent. Per
the master plan / `PLAN-TEMPLATE.md`, sub-agents implement and the
management session reviews the actual tests (confirm they assert
real expected values, not tautologies) before committing. Suggested
commits: one per step, or 5a+5c together (both small) and 5b
separately.

## Verification

- [ ] `make test-rust` passes with the new tests; report the new
      test names and counts.
- [ ] `chs_rounded_size` tests assert the qemu-verified pairs and
      the CHS self-consistency invariant (a regression in the CHS
      math would fail them without needing qemu).
- [ ] Read-primitive tests fail if the byte-accurate paths regress
      (verify by spot-reverting a fix mentally — e.g. dropping the
      tail would break the sub-sector-length test).
- [ ] `make lint` clean; `make check-binary-sizes` unaffected
      (test code is not compiled into guest binaries — confirm the
      new tests are `#[cfg(test)]` and don't bloat `convert.bin` /
      the guest crates' non-test builds).
- [ ] `pre-commit run --all-files` passes.
- [ ] Only `src/crates/vhd/src/lib.rs`,
      `src/crates/qcow2/src/lib.rs`, and `src/vmm/src/main.rs`
      changed.
- [ ] Commit messages follow conventions (model/context/effort).

## Hand-off

Remaining phases: 6 integration (consolidate the 14-row matrix /
edge cases beyond the per-phase tests already added in 3 and 4), 7
cross-version baselines, 8 coverage-guided fuzzing (the
`chs_rounded_size` and read-primitive functions are good fuzz
targets too), 9 differential fuzzing vs qemu-img dd (resolve the
`count=0 -O vhdx` limitation here), 10 docs. The
[[dd-qemu-img-parity-contract]] memory records the verified rules.
