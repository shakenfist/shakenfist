# Phase 8: coverage-guided fuzzing for `crates/measure/` and the scanners

Master plan: [PLAN-measure.md](/components/instar/plans/PLAN-measure/) ·
Previous phase: [PLAN-measure-phase-07-integration-tests.md](/components/instar/plans/PLAN-measure-phase-07-integration-tests/)

## Status: Not started

## Mission

Stand up two new `cargo-fuzz` (libFuzzer) targets under
`src/fuzz/fuzz_targets/` that exercise the code introduced in
phases 1 and 2:

1. **`fuzz_measure_calc.rs`** — pure-function fuzzing of the
   five `crates/measure/` calculators (`measure_raw`,
   `measure_qcow2`, `measure_vmdk`, `measure_vhd`,
   `measure_vhdx`). Decodes the fuzzer's input bytes into a
   `(target_format, AllocationSummary, options)` tuple, calls
   every function for every target, and asserts a small set of
   universal invariants. No I/O; no mock CallTable; the
   harness sits cleanly inside the calculator crate's signature.

2. **`fuzz_measure_scan.rs`** — image-bytes fuzzing of the
   per-parser `scan_allocation` entry points added in phase 2
   (`Qcow2State::scan_allocation`,
   `VmdkState::scan_allocation`,
   `VhdState::scan_allocation`,
   `VhdxState::scan_allocation`). Reuses the existing
   `instar_fuzz::build_call_table()` infrastructure to feed
   fuzzer-supplied bytes through the parser's cached sector
   reader and into the metadata-walking scan path.

Both targets are added to `src/fuzz/Cargo.toml` so the
existing CI workflow (`.github/workflows/coverage-fuzz.yml`)
picks them up alongside the 13 existing harnesses without any
workflow edits.

## Why this is its own phase

Phases 1-7 made `measure` work and proved it matches qemu-img
on a curated set of cases. Phase 8 hardens it against the
adversarial input space libFuzzer explores. The two
classes — pure calculator math and metadata-walking scanners
— need different harness shapes:

- **Calculators** are deterministic pure functions of
  bounded-size struct inputs. The harness's job is to
  decode fuzzer bytes into a structured tuple and call the
  function. Cheap, parallelisable, no I/O.
- **Scanners** consume parser state machines that follow on-
  disk pointers. The mock CallTable in
  `src/fuzz/src/lib.rs` already serves this exact need for
  the existing `fuzz_qcow2_l1l2`, `fuzz_vmdk_grain`, etc.
  targets; phase 8 reuses the infrastructure without
  changes.

Splitting from phase 9 (differential fuzzing extension) is
clean: phase 8 finds *crashes / hangs / panics*, phase 9 finds
*disagreements with qemu-img*. Different harnesses, different
oracles, different CI workflows.

## Architecture

### `fuzz_measure_calc.rs`: decoding the fuzz input

The calculators take `(virtual_size, allocated_bytes, options)`
tuples. We want libFuzzer to drive every code path, including
overflow checks, validation failures, and the qcow2 fixed-point
refcount loop. The simplest deterministic decoder:

```
byte 0      target format selector (mod 5)
            0 = raw, 1 = qcow2, 2 = vmdk, 3 = vhd, 4 = vhdx
byte 1      qcow2 refcount_bits selector (mod 8): picks one of
            {1, 2, 4, 8, 16, 32, 64, 0xff (invalid)}
byte 2      flag bits: bit 0 = extended_l2, bit 1 = lazy_refcounts,
            bit 2 = compat_v3, bit 3 = compress
byte 3      preallocation enum (mod 4): Off/Metadata/Falloc/Full
byte 4      vmdk subformat (mod 3): MonolithicSparse / StreamOpt / Flat
byte 5      vhd subformat (mod 2): Dynamic / Fixed
bytes 6-13  virtual_size (u64 little-endian)
bytes 14-21 allocated_bytes (u64 little-endian)
bytes 22-25 cluster_size (u32 LE)
bytes 26-29 grain_size (u32 LE)
bytes 30-33 block_size (u32 LE)
bytes 34-41 luks_header_overhead (u64 LE)
```

Total: 42 bytes minimum. Shorter inputs return early.

Including `0xff` as an "invalid" refcount_bits selector means
libFuzzer exercises the `InvalidOption` rejection path. Same
trick for non-power-of-two `cluster_size`/`grain_size`/`block_size`
values: the fuzzer naturally generates them and the calculator's
validation rejects them with `InvalidOption`.

### `fuzz_measure_calc.rs`: invariants

For every successful return (`Ok(MeasureOutput)`):

1. `required <= fully_allocated` (the *sparse* size is never
   larger than the *fully-allocated* size).
2. For `target = raw`: `fully_allocated >= virtual_size`
   (raw has no overhead, so this is exact equality, but `>=`
   lets the assertion survive future raw-padding changes).
3. For `target != raw`: `fully_allocated > 0` whenever
   `virtual_size > 0` (every non-raw format has at least
   header overhead).
4. Overflow sanity: `required + fully_allocated` doesn't wrap
   on `checked_add` — i.e. `required.checked_add(fully_allocated).is_some()`.
   Catches a class of latent overflow where each result is
   individually small but their sum exceeds `u64::MAX`.

Errors are *not* failures — the calculator is explicitly
allowed to return `MeasureError::Overflow / InvalidOption /
InvalidSize` and libFuzzer only catches panics. The
`Result::is_ok()` branch is where invariants apply.

### `fuzz_measure_scan.rs`: dispatch on a format prefix byte

```
byte 0      format selector (mod 4): 0=qcow2, 1=vmdk, 2=vhd, 3=vhdx
bytes 1..   image data, fed through set_fuzz_input()
```

The `raw` scanner is trivial (returns virtual_size as
allocated_bytes; no I/O); skipping it has zero coverage cost.

Per-format flow:

```rust
fuzz_target!(|data: &[u8]| {
    if data.len() < 2 { return; }
    let format = data[0] % 4;
    let image_data = &data[1..];
    instar_fuzz::set_fuzz_input(image_data);
    let call_table = instar_fuzz::build_call_table();
    let sector_size = 512;
    let input_capacity = instar_fuzz::input_capacity();

    // Two cache buffers — every scanner needs them, name varies.
    let mut cache_a = vec![0u8; shared::MAX_SECTOR_SIZE];
    let mut cache_b = vec![0u8; shared::MAX_SECTOR_SIZE];
    let mut bytes_read = 0u64;

    unsafe {
        match format {
            0 => { /* qcow2 */
                let state = qcow2::Qcow2State::init(
                    &call_table, 0, sector_size, input_capacity,
                    cache_a.as_mut_ptr(), cache_b.as_mut_ptr(),
                    &mut bytes_read,
                );
                if let Some(mut s) = state {
                    // Recover virtual_size by re-parsing the first sector.
                    let mut buf = [0u8; shared::MAX_SECTOR_SIZE];
                    if (call_table.read_input_sector)(0, 0, buf.as_mut_ptr(), sector_size) {
                        let hdr = qcow2::QcowHeader::parse(&buf[..sector_size]);
                        if let Some(h) = hdr {
                            let _ = s.scan_allocation(
                                &call_table, sector_size, input_capacity,
                                h.virtual_size, &mut bytes_read,
                            );
                        }
                    }
                }
            }
            1 => { /* vmdk */ ... }
            2 => { /* vhd */  ... }
            3 => { /* vhdx */ ... }
            _ => unreachable!(),
        }
    }
});
```

Invariants asserted when the scan returns `Some(summary)`:

- `summary.allocated_bytes <= summary.virtual_size`
- `summary.allocated_bytes >= 0` (free; type system enforces)
- No panic (libFuzzer's default oracle)

`None` returns are fine — the parser rejected the image.

### Cargo manifest additions

```toml
# src/fuzz/Cargo.toml

[[bin]]
name = "fuzz_measure_calc"
path = "fuzz_targets/fuzz_measure_calc.rs"
doc = false
test = false

[[bin]]
name = "fuzz_measure_scan"
path = "fuzz_targets/fuzz_measure_scan.rs"
doc = false
test = false

[dependencies]
# ... existing entries ...
measure = { path = "../crates/measure" }   # NEW
```

The `measure` crate must be added as a fuzz dependency; it's
currently not in the fuzz crate's manifest because
`crates/measure` only landed in phase 1.

### Existing CI workflow integration

`.github/workflows/coverage-fuzz.yml` enumerates targets via:
- `workflow_dispatch.inputs.targets` (manual override)
- Auto-discovery for the empty case (the script lists every
  `[[bin]]` entry in `Cargo.toml`)

So adding the two new `[[bin]]` entries is sufficient — no
workflow edit needed. Verify during 8b by reading the script
that emits the matrix; if it hard-codes target names, extend
it.

### Corpus seeding

Phase 8 ships with no curated corpus. libFuzzer starts with
an empty seed and discovers inputs on its own. For
`fuzz_measure_scan`, the existing
`scripts/extract-fuzz-corpus.py` already produces
format-prefixed inputs from `instar-testdata` for the other
scanner harnesses; phase 8's brief asks the script to be
extended only if a smoke run shows poor initial coverage.
Defer to 8b's verification step.

### Smoke runs

Each target's first run should be ~60s locally during step
8a/8b to confirm:
- It builds (`cargo +nightly fuzz build <target>`).
- It actually runs without immediate libFuzzer setup panics
  (`cargo +nightly fuzz run <target> -- -runs=10000 -max_total_time=60`).
- No crashes within the smoke window.

If any crashes appear, treat them as **real bugs** to file
and fix before committing the harness. A crashing harness
on first run probably means the fuzzer found a genuine panic
in `crates/measure/` or one of the scanners that the existing
unit tests didn't catch.

### What we are NOT fuzzing in this phase

- The host CLI surface (clap parsing) — that's standard
  library code, well-tested.
- The protobuf decoder — covered by existing
  `fuzz_qcow2_*` and friends transitively.
- The convert writer — out of scope; the measure-vs-convert
  round-trip is phase 7d's job.
- The `-o key=value,...` parser — string parsing of CLI args,
  could be added later as a small extra target.
- Cross-version baseline comparison — that's phase 9's
  differential fuzzer.

## Open questions

1. **Should `fuzz_measure_scan` exercise raw too?** Phase 2's
   `raw::scan_allocation` is `pub fn scan_allocation(virtual_size:
   u64) -> AllocationSummary`. It's pure, takes a single u64,
   and is trivial. Coverage benefit is near zero.
   Recommendation: skip; let `fuzz_measure_calc` cover it via
   the calculator dispatch.

2. **One combined harness vs two separate ones?** The two
   surfaces have different signatures (one takes structured
   bytes, the other takes raw image data) so a single
   harness would need a top-level dispatch on the first
   byte. Recommendation: two separate harnesses — cleaner
   coverage attribution, simpler corpus management,
   parallelism in the CI matrix.

3. **`fuzz_measure_calc` invariant for qcow2 `Preallocation`**:
   for `Falloc` / `Full` the calculator sets
   `required == fully_allocated`. Should the harness assert
   that? Recommendation: yes — adds a meaningful invariant
   for free. (Skip for `Metadata`, where the rule is
   subtler — phase 1's "metadata equals off" finding means
   the invariant would actually fire as `required ==
   off_required` not `metadata_required`. Pass-through is
   safest.)

4. **`fuzz_measure_scan` and the qcow2 virtual_size recovery**:
   the harness re-parses `QcowHeader` to extract
   `virtual_size` before calling `scan_allocation`. If the
   header parse fails the call is skipped. This matches the
   real measure operation's behaviour (phase 3f's guest
   binary does the same dance), but means a fuzzer can't
   directly drive `scan_allocation` with a bogus
   virtual_size. Recommendation: that's fine — the bogus-input
   coverage is already had via `cluster_lookup` fuzzing in
   the existing `fuzz_qcow2_l1l2` target.

5. **Should `crates/measure` get a `pub(crate)` helper for the
   refcount fixed-point loop**, exposed via a `#[cfg(fuzzing)]`
   gate so the harness can drive it directly? Recommendation:
   no — the loop is exercised through the public
   `measure_qcow2` entry point, and the fuzzer will explore
   it via the `(used_clusters, cluster_size, refcount_bits)`
   parameter space that `measure_qcow2` constructs internally.

6. **Sanitizers**: the existing fuzz harnesses run under
   libFuzzer's default address-sanitizer-equivalent. Phase 8
   inherits that; no additional sanitizer config needed.

7. **Nightly CI duration**: phase 6 uses `cron: '0 4 * * *'`
   with 1 h per target. Adding two new targets adds 2 h to
   nightly. Acceptable for the runner pool. Verify the
   workflow's `timeout-minutes: 480` ceiling still
   accommodates 15 targets × 1 h.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 8a | medium | sonnet | none | Create `src/fuzz/fuzz_targets/fuzz_measure_calc.rs` per the "decoding the fuzz input" + "invariants" sections above. Add `measure = { path = "../crates/measure" }` to `src/fuzz/Cargo.toml [dependencies]` and add the matching `[[bin]]` entry. The harness decodes the first 42 bytes into a structured tuple, dispatches to `measure_raw / measure_qcow2 / measure_vmdk / measure_vhd / measure_vhdx`, and asserts the four invariants only on `Ok` returns. Errors are silently ignored. Verify with `cd src/fuzz && cargo +nightly fuzz build fuzz_measure_calc` and then `cargo +nightly fuzz run fuzz_measure_calc -- -runs=10000 -max_total_time=60` (or however the project's local fuzz workflow runs — read `.github/workflows/coverage-fuzz.yml` for the canonical incantation). If crashes appear, **stop and file them as real bugs** before continuing. Touch only `src/fuzz/Cargo.toml` and the new target file. |
| 8b | medium | sonnet | none | Create `src/fuzz/fuzz_targets/fuzz_measure_scan.rs` per the "dispatch on a format prefix byte" section above. Format-selector byte chooses qcow2/vmdk/vhd/vhdx, remaining bytes fed through `instar_fuzz::set_fuzz_input` and the existing `build_call_table()` mock. For each format: call `<Format>State::init`, then `<Format>State::scan_allocation`; assert `allocated_bytes <= virtual_size` on `Some` return. Match the exact init signature variations the phase 2 scanners use (qcow2 takes l1+l2 cache; vmdk takes gd+gt; vhd takes bat+data; vhdx takes bat+data; qcow2's scan_allocation needs virtual_size recovered via `QcowHeader::parse`). Add the `[[bin]]` entry to `src/fuzz/Cargo.toml`. Same smoke-run verification as 8a. **If the CI workflow at `.github/workflows/coverage-fuzz.yml` enumerates targets by name rather than auto-discovering them**, also update that workflow's target list — but read it first; it likely auto-discovers via `cargo metadata` or a `ls fuzz_targets/` glob. |
| 8c | low | sonnet | none | Update `ARCHITECTURE.md`: the existing "Coverage-Guided Fuzzing" subsection (around the "Differential Fuzzing" area) lists 13 fuzz targets. Update the count to 15 and add `fuzz_measure_calc` / `fuzz_measure_scan` to the enumeration. Update `CHANGELOG.md` Unreleased / Added with: "Coverage-guided fuzz targets for `crates/measure/` (`fuzz_measure_calc`) and the per-parser `scan_allocation` entry points (`fuzz_measure_scan`). Picked up automatically by the nightly coverage-fuzz workflow. (PLAN-measure-phase-08-fuzz-coverage.md)". Run `pre-commit run --all-files`. Touch only ARCHITECTURE.md and CHANGELOG.md. |

Total: 3 commits.

## Out of scope for phase 8

- Differential fuzzing (phase 9 — that's a separate workflow
  comparing instar against qemu-img).
- Corpus seeding (defer to 8b verification; only add if
  initial coverage is poor).
- Refactoring `crates/measure` to expose internal helpers for
  fuzzing (none needed).
- `-o key=value,...` parser fuzzing (potential follow-up;
  small scope but not blocking).
- Extending the workflow's per-target time budget (1 h
  default is fine).

## Success criteria

- `src/fuzz/Cargo.toml` lists `fuzz_measure_calc` and
  `fuzz_measure_scan` `[[bin]]` entries plus the `measure`
  dependency.
- `cargo +nightly fuzz build fuzz_measure_calc` succeeds.
- `cargo +nightly fuzz build fuzz_measure_scan` succeeds.
- `cargo +nightly fuzz run <target> -- -runs=10000
   -max_total_time=60` completes without crashes for both
  targets.
- The nightly CI workflow's auto-discovery picks up the new
  targets (manual verification by reading
  `.github/workflows/coverage-fuzz.yml`).
- ARCHITECTURE.md and CHANGELOG.md updated.

## Risks and mitigations

- **Initial fuzz run finds a real panic**. Mitigation: 8a/8b
  briefs say "stop and file the bug". The expected outcome is
  no findings because phases 1 and 2 covered the math and
  scanner logic with extensive unit tests; if the fuzzer does
  find something, it's high-value signal worth pausing for.
- **Mock CallTable signature drift**: phase 2 left
  `Qcow2State::scan_allocation` taking `virtual_size` as an
  argument (not a field). The harness must call
  `QcowHeader::parse` to recover it; getting the offset wrong
  results in scan_allocation reading bogus values and
  potentially asserting. Mitigation: 8b's brief calls this out
  and points at phase 3f (the guest binary) for the canonical
  recipe.
- **CI workflow doesn't auto-discover targets**: if the
  workflow hard-codes the target list, 8b needs to extend
  it. Mitigation: 8b's brief says "read it first".
- **libFuzzer's default 4 GB memory limit**: phase 1's
  fixed-point loop is bounded at 16 iterations. The qcow2
  metadata-walking scanner could blow memory on adversarial
  L1 sizes. Mitigation: the existing
  `fuzz_qcow2_l1l2` target survives this, suggesting the
  scanner's bounds checks are adequate. If a crash hits the
  memory limit, file it as a real bug and add an explicit
  early-return in the scanner.

## Back brief

Before executing any step, the executing agent should
back-brief: which fuzz target file is being added, which
crates from phases 1/2 it exercises, and what invariants the
harness asserts. The reviewer should verify the harness
exercises every public entry point listed in the master
plan's success criteria for phase 8 — both calculators (5
functions) and scanners (4 non-raw functions).
