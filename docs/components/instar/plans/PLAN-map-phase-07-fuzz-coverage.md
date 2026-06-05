# Phase 7: coverage-guided fuzz harness for map iterators

Master plan: [PLAN-map.md](/components/instar/plans/PLAN-map/) ·
Previous phase: [PLAN-map-phase-06-integration-tests.md](/components/instar/plans/PLAN-map-phase-06-integration-tests/)

## Status: Complete

Both steps committed. `fuzz_map_iter` builds clean under
`make fuzz-build FUZZ_TARGET=fuzz_map_iter`. 60-second smoke
run (`make fuzz-run FUZZ_TARGET=fuzz_map_iter
FUZZ_DURATION=60`) reaches ~4M iterations with libFuzzer
reporting ongoing `NEW` coverage growth and zero crashes.
Auto-discovery in `.github/workflows/coverage-fuzz.yml`
picks up the new `[[bin]]` entry without a workflow edit.

## Mission

Add one libFuzzer-driven coverage-guided fuzz target,
`src/fuzz/fuzz_targets/fuzz_map_iter.rs`, that exercises the
per-format `map_extents()` walkers added in phase 1 against
arbitrary fuzz-driven byte input through the existing
`instar_fuzz` mock `CallTable`. The target dispatches on a
format prefix byte (qcow2/vmdk/vhd/vhdx), drives the walker to
completion with a recording closure, then asserts a
**partition invariant** on the emitted extents: every byte of
`[0, virtual_size)` is covered by exactly one extent, with no
overlaps, no gaps, no zero-length records, and no
`start + length` overflow.

The partition invariant is the bug-finder. The existing
`fuzz_measure_scan` proves only that the summed
`allocated_bytes` doesn't exceed `virtual_size`; it cannot see
an off-by-one at a cluster boundary that the summary
arithmetic happens to balance. `map_extents` exposes the
per-cluster classification directly, so the same fuzz input is
a stricter assertion of correctness.

Phase 7 ships only the harness. No production-code changes
are required — phase 1 already gave each parser a
`map_extents()` entry point, and `instar_fuzz::build_call_table`
already supplies the sector-reader mock used here. If the
harness *does* find a bug on its first run, the bug fix is
filed as a follow-up against the relevant parser crate, not
inside this phase.

## Why this is its own phase

- The partition invariant is qualitatively different from
  `fuzz_measure_scan`'s summary invariant and warrants its own
  target so failures can be triaged per-format.
- One libFuzzer corpus per target keeps coverage tractable.
  Folding map walking into `fuzz_measure_scan` would mix two
  different shapes of assertion (summary range check vs.
  partition equality) into one corpus and dilute the discovery
  rate for both.
- Splitting from phase 8 (differential fuzzing against
  `qemu-img map`) keeps the deterministic in-process invariant
  separate from the cross-binary comparison campaign. Phase 7
  finds parser bugs; phase 8 finds output-formatting and
  classification disagreements.
- The harness is small (~150 lines) and self-contained, so it
  fits in a single commit alongside its manifest entry.

## Architecture

### Target layout

One new file: `src/fuzz/fuzz_targets/fuzz_map_iter.rs`. One
new `[[bin]]` entry in `src/fuzz/Cargo.toml`. No new fuzz
dependencies — every required crate (`shared`, `qcow2`,
`vmdk`, `vhd`, `vhdx`) is already in the fuzz manifest from
PLAN-measure phase 8.

`raw` is intentionally omitted from the format dispatch. Its
`map_extents` is a pure function `(virtual_size: u64) -> emits
one Data extent of length virtual_size` (see
`src/crates/raw/src/lib.rs:73`). There is no on-disk input to
fuzz — the only argument is a `u64`. The trivial
all-allocated shape is covered by the existing raw-related
fuzz targets and is unit-tested in
`src/crates/raw/src/lib.rs` already. Documenting the omission
matches the same decision in `fuzz_measure_scan` (where raw is
covered by `fuzz_measure_calc` instead).

### Input layout

```
data[0]      : format selector (data[0] % 4 → 0=qcow2,
               1=vmdk, 2=vhd, 3=vhdx)
data[1..]    : image bytes fed via instar_fuzz::set_fuzz_input
```

Minimum input length: 2 bytes. Inputs shorter than that
return immediately.

The single-byte selector matches `fuzz_measure_scan`'s layout
exactly. We do not reserve a second config byte (sector size,
walker cap, etc.) in v1 — the harness uses fixed defaults to
keep the input space tight and let libFuzzer find boundary
cases against a single configuration. Variations can be added
in follow-up work if coverage stalls.

### Walker dispatch

Mirrors `fuzz_measure_scan` structurally — same `init` calls,
same `cache_a` / `cache_b` / `bytes_read` plumbing — but
substitutes `map_extents` for `scan_allocation` and feeds in a
recording closure rather than expecting a summary return.

```rust
#![no_main]
use libfuzzer_sys::fuzz_target;
use shared::{MapExtent, MapExtentState};

fuzz_target!(|data: &[u8]| {
    if data.len() < 2 {
        return;
    }
    let format = data[0] % 4;
    let image_data = &data[1..];

    instar_fuzz::set_fuzz_input(image_data);
    let call_table = instar_fuzz::build_call_table();
    let sector_size = 512usize;
    let input_capacity = instar_fuzz::input_capacity();

    let mut cache_a = vec![0u8; shared::MAX_SECTOR_SIZE];
    let mut cache_b = vec![0u8; shared::MAX_SECTOR_SIZE];
    let mut bytes_read = 0u64;

    // Recording closure. Records into a Vec we own here.
    // A per-call cap stops the harness from OOMing on
    // pathological inputs that emit unbounded extents (which
    // would itself be a bug, but a timeout is friendlier
    // than running the OOM killer on the CI host).
    let mut extents: Vec<MapExtent> = Vec::with_capacity(1024);
    const EXTENT_CAP: usize = 1 << 20; // 1 M extents
    let mut hit_cap = false;
    let mut emit = |e: MapExtent| -> bool {
        if extents.len() >= EXTENT_CAP {
            hit_cap = true;
            return false; // abort the walk
        }
        extents.push(e);
        true
    };

    let virtual_size: Option<u64> = unsafe {
        match format {
            0 => qcow2_dispatch(&call_table, sector_size,
                input_capacity, &mut cache_a, &mut cache_b,
                &mut bytes_read, &mut emit),
            1 => vmdk_dispatch(...),
            2 => vhd_dispatch(...),
            _ => vhdx_dispatch(...),
        }
    };

    // If the walker rejected the input, no invariant to check.
    let Some(virtual_size) = virtual_size else { return; };
    if hit_cap {
        // Emitting >1M extents on a fuzz input is itself
        // suspicious. Surface it as a failure so a real
        // unbounded-loop bug is not silently swallowed.
        panic!("map_extents emitted >{EXTENT_CAP} extents on \
                {} bytes of input — likely unbounded loop or \
                pathological cluster size", image_data.len());
    }
    assert_partition(&extents, virtual_size);
});
```

Each `*_dispatch` helper returns `Some(virtual_size)` when the
parser successfully opened the source and the walk returned
`Some(())`, and `None` otherwise. Pulling `virtual_size` out
of the state is straightforward: `VmdkState.capacity_sectors *
512`, `VhdState.current_size`, `VhdxState.virtual_disk_size`
are all `pub`. For qcow2 we re-read the header via
`QcowHeader::parse(&first_sector)` exactly like
`fuzz_measure_scan.rs:60-65` already does.

If `*_dispatch` returns `None`, the harness silently bails
without checking the invariant — that's the equivalent of
"parser rejected the image", which is a valid outcome.

### The partition invariant

Given a vec of extents `e[0..n]` and the source's
`virtual_size`, the invariant is:

1. **No zero-length extents.** `e[i].length > 0` for every
   `i`. (Coalescer is supposed to drop zeros; if one leaks
   through, that's a bug.)

2. **No `start + length` overflow.** Every
   `e[i].start.checked_add(e[i].length)` is `Some`.

3. **Sorted and contiguous.** `e[0].start == 0`. For every
   `i ≥ 1`, `e[i].start == e[i-1].start + e[i-1].length`.
   (Equivalent: no gaps, no overlaps.)

4. **Closes at `virtual_size`.** `e[n-1].start + e[n-1].length
   == virtual_size`.

5. **Special case `virtual_size == 0`.** `n == 0` is required;
   any extent emitted for a zero-virtual-size image is a bug.

6. **Special case `n == 0` with `virtual_size > 0`.** This is
   a bug — the walker promised to partition a non-empty range
   and emitted nothing.

The invariant is *not* checked on:

- `Some(virtual_size)` returns where the walker aborted early
  via `emit → false`. The harness's closure only returns
  `false` on hitting the extent cap (which itself fails the
  harness above before the invariant is asserted), so this is
  in practice never triggered.
- I/O failures. Those return `None` from the dispatcher and
  bail before the assertion.

### File-offset overflow invariant (secondary)

Less load-bearing than partition, but cheap to check:

7. **No file-offset overflow on Data extents.** For every
   `MapExtentState::Data { file_offset }`, the value
   `file_offset.checked_add(e.length)` is `Some`. This catches
   a class of qcow2 / vmdk bug where a malformed cluster
   offset combines with a long coalesced run to produce a
   wrapped file offset.

We do *not* assert that file offsets are unique across
extents (compressed-cluster reporting in v1 can repeat file
offsets across logically-distinct extents) nor that they fit
within `input_capacity * sector_size` (clamping is the
walker's responsibility, but a fuzz-driven adversarial input
may legitimately point past the file).

### Why one target and not five

Each format already has its own header / metadata fuzz target
(`fuzz_qcow2_header`, `fuzz_vmdk_header`, etc.) and the
existing convention is to consolidate "scan one parser of N"
into a single dispatch-by-prefix target
(`fuzz_measure_scan` does exactly this). Following that
convention here keeps the corpus structure consistent and
makes follow-up extensions (a second harness for
backing-chain composition, say) obvious by analogy. If per-
format coverage stalls in production fuzzing, splitting later
is a mechanical refactor.

### CI integration

`.github/workflows/coverage-fuzz.yml` enumerates targets via
the workflow's auto-discovery path: every `[[bin]]` entry in
`src/fuzz/Cargo.toml` is picked up automatically (verified in
PLAN-measure phase 8b). No workflow edit is required —
adding the `[[bin]]` entry is sufficient.

If 7a's smoke run reveals that auto-discovery has regressed
(or the workflow script hard-codes target names), the fix is
a one-line addition to the script; the brief calls this out
explicitly.

### Corpus seeding

No curated corpus in v1. libFuzzer starts empty and discovers
inputs on its own. The qcow2 / vmdk / vhd / vhdx header
parsers are already well-covered by their respective
header-fuzz targets, so coverage into the metadata walks is
the bottleneck — addressed by sharing the same byte-shape
inputs as `fuzz_measure_scan` (which uses the existing
`scripts/extract-fuzz-corpus.py` outputs). If 7a's smoke run
shows poor early coverage, extending the corpus extractor to
seed `fuzz_map_iter` from the same prefix-encoded testdata
images is a one-liner addition — defer to a 7b follow-up if
the smoke run says it's needed.

### Smoke run during 7a

Local smoke run (after `cargo +nightly fuzz build
fuzz_map_iter`):

```
cd src/fuzz
cargo +nightly fuzz run fuzz_map_iter -- -runs=10000 -max_total_time=60
```

Acceptance:

- Builds without warnings under
  `RUSTFLAGS="-D warnings" cargo +nightly fuzz build`.
- 60-second smoke run completes without crashes.
- Coverage growth visible in libFuzzer's per-line output
  (`NEW` lines appearing across the 60s window) — confirms
  the harness reaches into the metadata walks.

If a crash *does* appear in the smoke window, file as a real
parser bug under PLAN-fuzzing-bugs.md's existing tracking
flow (the analogue of how `fuzz_measure_scan` surfaced 10
qcow2 issues in commit `6de9687`) and fix before committing
the harness. Phase 7 ships with a clean smoke window or
not at all.

## Open questions

1. **Should `fuzz_map_iter` exercise raw?** No — raw's
   `map_extents` is a pure function of `virtual_size`. There's
   no on-disk input surface to fuzz. The trivial output is
   pinned by unit tests in `src/crates/raw/src/lib.rs`. The
   same omission applies in `fuzz_measure_scan`.

2. **Per-format target split vs. dispatch-on-prefix?**
   Dispatch on prefix matches the established convention
   (`fuzz_measure_scan`). Splitting later if coverage stalls
   is mechanical.

3. **What about a second target for `MapExtentCoalescer`
   directly?** `MapExtentCoalescer` is a pure function from a
   sequence of pushes to a sequence of emitted records. It is
   exercised through the per-format walks; a dedicated
   coalescer fuzz target would duplicate that coverage. The
   `cargo test` unit tests in `src/shared/src/lib.rs` (lines
   ~3989+) already pin the merging logic case by case.
   **Recommendation**: don't add a dedicated coalescer
   target; the integration via `fuzz_map_iter` is enough.

4. **`emit` closure aborting on `EXTENT_CAP`**: this changes
   the walker's exit path (it returns `Some(())` with extents
   truncated). Should the partition invariant be checked
   against that truncated set? **Recommendation**: no — the
   harness `panic!`s when the cap is hit before reaching the
   invariant check, so the partition assertion always runs
   against a complete walk. `EXTENT_CAP` is OOM-prevention,
   not a fuzzer signal.

5. **What's the right `EXTENT_CAP`?** 1 M extents at ~32 bytes
   per `MapExtent` is ~32 MiB resident — acceptable on CI
   hosts, generous enough that legitimate fragmented sources
   never hit it (a 4 GiB qcow2 at 4 KiB clusters maxes out at
   1 M extents, which is the theoretical worst case before
   coalescing). **Recommendation**: 1 M. Revisit only if a
   real fuzz finding tunes it.

6. **Compressed clusters and file-offset overflow check**:
   compressed extents reuse a file offset across coalesced
   runs in v1 (instar treats them as Data without the
   high-bit-set marker; see `docs/quirks.md` map quirks).
   This means invariant 7 (file_offset + length doesn't
   overflow) can spuriously fail for legitimate compressed-
   cluster sources. **Recommendation**: scope invariant 7 to
   non-compressed clusters only by skipping it when the
   walker is qcow2 *and* the L2 entry's compressed bit was
   set — but the harness doesn't have visibility into the
   compressed bit. **Pragmatic alternative**: drop invariant
   7 entirely in v1. The partition invariants (1-6) are the
   high-value finding; invariant 7 can be added in follow-up
   work once compressed-cluster handling lands properly.
   **Decision**: drop invariant 7 from v1. Document the
   omission inline.

7. **Should the harness pin sector_size?** The phase 1
   walkers accept `sector_size` as an argument and the
   real guest passes 512. Fuzz with 512 only in v1 to keep
   the corpus tight; sector_size 4096 can be added in a
   follow-up. (`fuzz_measure_scan` uses the same single-
   sector-size approach.)

8. **Interaction with `bytes_read` accounting**: the walker
   updates `bytes_read` as a `*mut u64` for measure's
   bandwidth accounting. The fuzz harness doesn't care
   about the value but must pass through a valid pointer.
   Mirrors `fuzz_measure_scan`.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | medium | sonnet | none | Create `src/fuzz/fuzz_targets/fuzz_map_iter.rs` following the structure in the Architecture section. Four dispatch arms (qcow2/vmdk/vhd/vhdx), each pulling `virtual_size` from the appropriate `pub` field (`VmdkState.capacity_sectors * 512`, `VhdState.current_size`, `VhdxState.virtual_disk_size`) or re-parsing the header for qcow2 (mirror `fuzz_measure_scan.rs:60-65`). Recording closure pushes into a `Vec<MapExtent>` with `EXTENT_CAP = 1 << 20`, `panic!` on cap-hit before invariant checking. Implement `assert_partition(&extents, virtual_size)` as a helper at the bottom of the file enforcing invariants 1-6 from the Architecture section (drop invariant 7 in v1 per Open Question 6). Add a `[[bin]]` entry for `fuzz_map_iter` to `src/fuzz/Cargo.toml` in the "CallTable-dependent targets" section, ordered alphabetically with the rest. **Verify the build**: `cd src/fuzz && cargo +nightly fuzz build fuzz_map_iter`. **Smoke run** (60s): `cd src/fuzz && cargo +nightly fuzz run fuzz_map_iter -- -runs=10000 -max_total_time=60`. Acceptance: builds clean under `RUSTFLAGS="-D warnings"`, smoke run completes without crashes, libFuzzer reports `NEW` coverage lines across the window. If a crash appears: **stop**, file as a real parser bug, fix before committing. Do not commit until the smoke window is clean. |
| 7b | low | sonnet | none | Update `ARCHITECTURE.md` "Fuzzing" / "Coverage-guided fuzz harnesses" section to mention `fuzz_map_iter` alongside `fuzz_measure_scan` (one sentence: the partition-invariant assertion is the distinguishing feature). Update `CHANGELOG.md` Unreleased / Added with a one-line entry. Update the master plan's Execution table row 7 from "Not started" to "Complete" with a brief note (e.g. "Complete (fuzz_map_iter target + 60s smoke run; no crashes)"). Run `pre-commit run --all-files`. |

Total: 2 commits.

### Why no high-effort step

The harness design is the high-effort part — capturing the
partition invariant correctly, deciding which sub-invariants
to drop in v1, picking the cap, deciding which formats to
include. That work is done in this plan. The implementation
is mechanical following `fuzz_measure_scan` as the template.
Sonnet with a detailed brief is the right tool.

The "opus for harness design" advice in the master plan's
phase-7 brief refers to *this plan*, which I am writing at
opus / high-effort. Step execution is sonnet.

### Out of scope for phase 7

- Differential comparison against `qemu-img map` (phase 8).
- Per-format split targets (deferred; one-target dispatch
  matches the established convention).
- Dedicated `MapExtentCoalescer` fuzz target (covered
  transitively).
- Compressed-cluster file-offset overflow invariant
  (deferred to a follow-up once compressed-cluster reporting
  is fixed).
- Sector-size variations (`fuzz_measure_scan` pins 512;
  same here).
- Curated corpus seeding (deferred; the smoke run
  determines whether seeding is necessary).
- Backing-chain composition fuzzing (v1 refuses chain
  sources host-side; chain support is a future-work item
  in PLAN-map.md).
- Raw `SEEK_HOLE` sparseness fuzzing (raw's `map_extents`
  is pure-of-virtual_size in v1; future-work item).

## Success criteria

- `src/fuzz/fuzz_targets/fuzz_map_iter.rs` exists and
  implements the dispatch + partition-invariant structure
  above.
- `src/fuzz/Cargo.toml` has the new `[[bin]]` entry.
- `cargo +nightly fuzz build fuzz_map_iter` succeeds.
- A 60-second smoke run completes without crashes and shows
  libFuzzer coverage growth.
- `make lint` / `make test-rust` remain clean (the new file
  is `no_main` and doesn't affect the rest of the workspace).
- `pre-commit run --all-files` clean.
- `ARCHITECTURE.md` and `CHANGELOG.md` mention the new
  target.
- The master plan's Execution table marks phase 7 Complete.

## Risks and mitigations

- **Smoke run uncovers a real parser bug.** Most likely in
  the qcow2 or vhdx walker — those are the parsers whose
  classification logic is most adversarial-input-sensitive.
  Mitigation: treat it as a real find, file under
  PLAN-fuzzing-bugs.md, fix before committing. The brief
  explicitly tells the sub-agent to stop and surface
  rather than commit-around. (This is the same path
  `fuzz_measure_scan` took: it surfaced 10 issues in
  commit `6de9687` which were then fixed.)

- **Coverage growth stalls quickly.** If the smoke run shows
  libFuzzer hitting a coverage plateau within the 60s window
  (no `NEW` lines after the first few seconds), the corpus
  needs seeding. Mitigation: extend
  `scripts/extract-fuzz-corpus.py` to emit format-prefix-
  encoded inputs for `fuzz_map_iter` (the existing
  `fuzz_measure_scan` does this); flag as 7c follow-up if
  the smoke run says it's needed.

- **`EXTENT_CAP` is too tight.** A legitimate fragmented
  source (4 GiB qcow2 at 4 KiB clusters) maxes at 1 M
  extents. If a smoke-run input hits the cap on a legitimate
  source, raise the cap or coalesce more aggressively.
  Mitigation: 1 M is generous for the corpus shapes the
  fuzz mock supports (small input_capacity, ~MiB range).
  Real-world fragmented sources are out of scope of in-
  process fuzzing.

- **The partition invariant has an edge case the walkers
  weren't designed for.** Specifically: zero-virtual-size
  sources, single-extent sources, sources where the walker
  emits a hole spanning all of `[0, virtual_size)`. The
  invariant must accept all of these. Mitigation: the
  Architecture section enumerates the special cases
  explicitly (5, 6). The harness sub-agent should re-read
  them before writing `assert_partition`.

- **`bytes_read` accounting drift between formats.** The
  parameter is `*mut u64` not `&mut u64`; passing `&mut
  bytes_read as *mut u64` is the established pattern but
  trivially wrong to write. Mitigation: mirror
  `fuzz_measure_scan.rs:42` line-for-line.

- **`virtual_size` extraction differs per format.**
  Documented in the Architecture section. For qcow2 the
  parser doesn't expose it on `Qcow2State`; the harness
  re-reads sector 0 and re-parses the header. For the other
  three, a `pub` field on the State suffices. The brief
  names the exact accessors.

## Back brief

Before executing step 7a, the sub-agent should back-brief:

- The file being created (`src/fuzz/fuzz_targets/fuzz_map_iter.rs`),
  the file being edited (`src/fuzz/Cargo.toml`), and the
  template being followed (`fuzz_measure_scan.rs`).
- The seven invariants enumerated in the Architecture
  section, and which ones are dropped in v1 (invariant 7,
  per Open Question 6).
- The exact accessors for `virtual_size` per format (qcow2:
  re-parse header; vmdk: `state.capacity_sectors * 512`;
  vhd: `state.current_size`; vhdx: `state.virtual_disk_size`).
- The smoke-run command and acceptance criteria, and the
  rule that a crash in the smoke window stops the commit
  and triggers a real bug-fix flow rather than a harness
  workaround.

The reviewer should verify:

- The dispatch arms match `fuzz_measure_scan` structurally
  (init, cache buffers, bytes_read).
- The recording closure correctly handles the cap-hit case
  (returns `false` to abort the walk; the post-walk check
  panics before `assert_partition` runs).
- `assert_partition` handles `virtual_size == 0` (expects
  zero extents) and `n == 0 && virtual_size > 0` (panics)
  as distinct cases.
- The smoke run ran for ≥60s, completed clean, and showed
  coverage growth.
