# PLAN-bitmap phase 09: coverage + differential fuzzing

## Prompt

Before responding to questions or discussion points in this
document, explore the codebase thoroughly. Read the actual code and
ground every claim in it — the coverage-fuzz harness
(`src/fuzz/Cargo.toml`, `src/fuzz/fuzz_targets/*.rs`,
`src/fuzz/src/lib.rs`), the nightly config
(`.github/workflows/coverage-fuzz.yml`, `tools/ci/fuzz-tier.sh`),
the differential fuzzer (`scripts/differential-fuzz.py`), the
amend fuzz phase plan (`docs/plans/PLAN-amend-phase-08-fuzz.md`,
the direct template — it found the amend cluster-size class of
bug), the Phase-1 qcow2 bitmap parsers
(`src/crates/qcow2/src/bitmap.rs`) and the Phase-3 bitmap planner
crate (`src/crates/bitmap/`). Do not speculate. Flag uncertainty.

Phase plans live in `docs/plans/` as
`PLAN-bitmap-phase-NN-<descriptive>.md`. The master plan is
[PLAN-bitmap.md](/components/instar/plans/PLAN-bitmap/); Phases 1–8 are complete —
`instar bitmap` runs end-to-end, passes a 44-test `qemu-img`
integration suite, and has cross-version baselines. This is the
ninth of ten phases. One commit per logical change.

## Situation

This phase adds fuzzing: **coverage-guided** (libFuzzer) targets for
the pure Phase-1 parsers and the Phase-3 planner, and a
**differential** `op_bitmap` in `differential-fuzz.py` that runs
random bitmap op sequences through both instar and `qemu-img` and
compares. It is a **fuzzing-infra + config phase** — the only
production changes would be *fixes* for panics/divergences the
fuzzers find (as amend's fuzz phase found the cluster-size bug).

The Phase-1 parsers and Phase-3 planner were written **panic-free
by construction** (length-guarded reads, `checked_*`/saturating
arithmetic) *specifically* to survive this phase — so coverage
fuzzing is the validation that the discipline held, and the highest
attacker-controlled surface (bitmap directory/table bytes parsed
from a hostile image) gets its adversarial test. The differential
fuzzer is the end-to-end net at scale, complementing Phase 7's
fixed matrix with randomized cluster sizes, names, granularities,
and op sequences.

Grounding (verified against the phase-9 research, HEAD `385467d`):

- **Coverage harness** (`src/fuzz/`): `Cargo.toml` has a flat
  `[dependencies]` of path deps + one `[[bin]]` per target (27
  today; e.g. `fuzz_amend_planners`). `qcow2` is **already** a fuzz
  dep (so the bitmap *parsers* need no new dep); `bitmap` is **not**
  yet a dep. The mock CallTable in `src/fuzz/src/lib.rs`
  (`build_call_table`, `set_fuzz_input`, `input_capacity`,
  `mock_read_input_sector`) **already wires
  `send_bitmap_result: mock_send_bitmap_result`** — no lib.rs edit
  needed.
- **Parser target shape** (`fuzz_qcow2_header.rs`): `#![no_main]`,
  `fuzz_target!(|data: &[u8]| { … })`, feed raw bytes to the
  parser(s), oracle = no panic.
- **Planner target shape** (`fuzz_amend_planners.rs`): a
  `thread_local! SCRATCH`, a fixed control-byte prefix decoding a
  `cluster_size` (from a `CLUSTER_SIZES` table) + flags, a
  synthesised header/structure, then the pure planner call with
  invariant assertions **on `Ok` only** (`check_amend_invariants`:
  patch bounds, no overlap, resulting-version sanity). **But the
  bitmap planner is not amend-shaped** — `action_add(old_dir,
  old_len, nb_bitmaps, name, granularity_bits, refblocks: &mut
  [u8], cursor: &mut AllocCursor, geom: &BitmapGeometry, out_dir:
  &mut [u8]) -> Result<ActionOutcome, BitmapError>` takes a
  synthesised directory + refblocks + geometry, so input-building
  follows `fuzz_snapshot_refcount.rs` (which builds refblocks +
  geometry from fuzz bytes), while the invariant/registration
  mechanics follow `fuzz_amend_planners.rs`.
- **Build/run** (`Makefile:515-553`, dockerized): `make fuzz-build
  FUZZ_TARGET=<t>`, `make fuzz-run FUZZ_TARGET=<t>
  FUZZ_DURATION=<s>`. Corpus at `src/fuzz/corpus/<target>/`; seeds
  via `scripts/extract-fuzz-corpus.py`.
- **Nightly registration** (coverage): edit **(1)**
  `src/fuzz/Cargo.toml` (add `bitmap` dep + `[[bin]]` per target),
  **(2)** `.github/workflows/coverage-fuzz.yml` — add each target to
  the `TARGETS=(...)` array (`:227-256`) and bump `N_TARGETS=27`
  (`:156`), **(3)** `tools/ci/fuzz-tier.sh` — add fast-saturating
  targets to `FAST_TIER=(...)`. (amend's phase-8 predates the
  tiering; do not forget step 3.)
- **Differential fuzzer** (`scripts/differential-fuzz.py`):
  `OPERATIONS` list (`:50-52`), `run_iteration`'s `if/elif op ==`
  dispatch (`:3427-3498`), `op_amend`/`_amend_option_picker`
  (`:1723`/`:1659`) as the model, reusable helpers
  `run_instar`/`run_qemu_img` (`:172`/`:185`), `compare_exit_codes`
  (`:490`), `_normalise_create_info` (`:1188`), `_qemu_check_metrics`
  (`:3207`) + `_is_clean` (`:3237`). To register `op_bitmap`: add
  `'bitmap'` to `OPERATIONS`, write `_bitmap_option_picker` +
  `op_bitmap`, add the dispatch arm — **the nightly
  (`differential-fuzz.yml`) runs all of `OPERATIONS` with `--device
  /dev/kvm`, so no workflow edit is needed** (op_bitmap launches a
  guest VMM, like op_amend).
- **Resolved uncertainties**: Phase 5 is landed (the host CLI works;
  44/44 integration tests). `qemu-img 10.0.8` has `bitmap` (Phase 7
  used it as the oracle). The only *bitmap awareness* in the
  differential script today is a `bitmaps`-presence check in
  `op_measure` (`:1031-1057`); a dedicated bitmaps-array comparator
  is net-new.

## Mission and problem statement

1. **Coverage targets** (`src/fuzz/`):
   - `fuzz_bitmap_parse` — feed raw `&[u8]` to the Phase-1 qcow2
     bitmap parsers: `qcow2::bitmap::parse_bitmaps_extension`,
     `parse_bitmap_dir_entry`, `decode_bitmap_table_entry` /
     `validate_bitmap_table_entry`, the geometry helpers, and (via
     the mock CallTable) `for_each_bitmap_entry`. Oracle: **no
     panic** on any bytes (the whole point of the panic-free
     discipline). Uses the existing `qcow2` fuzz dep.
   - `fuzz_bitmap_planners` — synthesise a directory buffer +
     refblocks + `BitmapGeometry` + `AllocCursor` from fuzz bytes
     (model input-building on `fuzz_snapshot_refcount.rs`), then
     drive the Phase-3 actions (`action_add/remove/clear/enable/
     disable`), the directory builders, and the merge helpers
     (`merge_cluster_action`, `or_bitmap_data` — fold in, they're
     tiny), asserting invariants on `Ok`: outcome offsets/
     `table_clusters_to_zero` in-bounds, serialized directory length
     consistent with `directory_byte_len`, no refcount value out of
     range, no panic. Needs a new `bitmap` fuzz dep.
   Register both (Cargo.toml `[[bin]]`, `coverage-fuzz.yml`
   `TARGETS` + `N_TARGETS`, `fuzz-tier.sh` `FAST_TIER`), and run each
   locally for a short burst to shake out immediate panics.

2. **Differential `op_bitmap`** (`scripts/differential-fuzz.py`):
   `_bitmap_option_picker(rng)` builds a random **valid** op
   sequence (respecting instar's contract so instar and qemu agree
   — see Open question 3); `op_bitmap` creates two identical random
   v3 qcow2 start images (random cluster size), applies the sequence
   to each (instar vs `qemu-img`) invocation-by-invocation, and
   after each compares exit codes, the `qemu-img info --output=json`
   **bitmaps array** (name/granularity/flags, via a new
   `_normalise_bitmaps_info` or extending `_normalise_create_info`),
   and `_qemu_check_metrics` + `_is_clean` (structural integrity).
   Add `'bitmap'` to `OPERATIONS` + the dispatch arm. Run locally
   (`--ops bitmap`, tens of iterations) and fix/register any
   divergence.

3. Any panic or divergence the fuzzers surface is a **product bug
   fixed in the crate/guest/host** (with a regression test), or —
   if it is an intentional instar-vs-qemu difference — steered
   around by the picker and recorded in the divergence registry
   (mirroring amend's zstd-downgrade handling). The nightly runs
   pick up both once registered.

**Out of scope:** docs (Phase 10); the actual long-running nightly
campaign (this phase registers + smoke-runs; the campaign runs in
CI). No changes beyond the fuzz targets/config/script + any bug fix.

## Open questions

### 1. How many coverage targets (2)

`fuzz_bitmap_parse` (parsers, `qcow2` dep, highest attacker-surface)
+ `fuzz_bitmap_planners` (planner, new `bitmap` dep). Fold the merge
helpers into the planner target. This matches snapshot's 2-target
split; amend used 1. Confirm 2 is right (don't over-split).

### 2. Planner-target input synthesis

The planner needs a *plausible* directory + refblocks + geometry or
it just bounces off validation without covering the interesting
alloc/free paths. Follow `fuzz_snapshot_refcount.rs`: decode
`cluster_size` from a control byte, build a refblock buffer marking
a metadata prefix occupied + a free run (so the allocator can
succeed), synthesise 0..N directory entries from the fuzz pool (some
valid, some malformed to exercise the parse-reject paths), then
apply a fuzz-chosen action. Assert the Phase-6-style refcount +
directory invariants on `Ok`. Keep a `thread_local!` scratch to
avoid per-run allocation.

### 3. Differential picker — respect instar's contract (parity)

For clean differential parity, `_bitmap_option_picker` must generate
sequences where instar and qemu **agree**, i.e. avoid the registered
divergences: **no `-b/-F`** (cross-file merge unsupported), **no
`--merge` mixed with metadata actions in one invocation** (instar
requires merge to be sole), **no non-16-bit `refcount_bits`** create
option, **valid granularity** (power of two in [512,2G]), **≤ 8
actions / ≤ 8 merge sources**, **names ≤ 1023 bytes**, and a
`--merge` source that **exists** (add it in an earlier invocation).
Model the discipline on `_amend_option_picker`'s divergence-
avoidance. Register the avoided differences in a
`KNOWN_BITMAP_DIFFERENTIAL_DIVERGENCES` note (and/or reuse the
existing `KNOWN_BITMAP_DIVERGENCES` rationale). Any *unexpected*
divergence is a real bug.

### 4. op_bitmap oracle depth — info + check (not the bits-set oracle)

`op_bitmap` compares `qemu-img info` (bitmaps metadata) + `qemu-img
check` (structural), exactly like `op_amend`. The **bits-set merge**
correctness is already validated bit-for-bit by Phase 7's
storage-daemon read-back oracle; replicating that per fuzz iteration
(a daemon launch each time) is too heavy and unnecessary here.
Recommend info + check only; note the bit-content coverage lives in
Phase 7. Confirm.

### 5. Fast-tier vs deep-tier

Both bitmap targets have small, quickly-saturating input spaces (a
parser and a bounded planner), so both belong in `FAST_TIER`
(`tools/ci/fuzz-tier.sh`). Confirm — if a target proves slow to
saturate, leave it in the deep tier.

### 6. Local smoke-run duration

Short local bursts (e.g. 60–180 s per coverage target;
~30–100 iterations of the differential) are enough to shake out
immediate panics/divergences in this phase; the real campaign is the
nightly. Note the durations used and any finding.

## Step-level guidance

Plan at **high** effort for 9a/9b (fuzz harness input-synthesis and
the differential oracle are subtle, and this phase can surface real
bugs — treat failures as product bugs) and **medium** for 9c.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 9a | high | opus | none | Coverage targets. Create `src/fuzz/fuzz_targets/fuzz_bitmap_parse.rs` (raw `&[u8]` → the `qcow2::bitmap` parsers + geometry helpers; use the mock CallTable from `instar_fuzz` for `for_each_bitmap_entry` — study `fuzz_qcow2_l1l2.rs` for the `set_fuzz_input`/`build_call_table` pattern) and `src/fuzz/fuzz_targets/fuzz_bitmap_planners.rs` (synthesise directory+refblocks+`BitmapGeometry`+`AllocCursor` from fuzz bytes modelled on `fuzz_snapshot_refcount.rs`; drive the Phase-3 actions + directory builders + merge helpers; assert Phase-6-style invariants on `Ok`, panic-free otherwise). Register: `src/fuzz/Cargo.toml` (`bitmap = { path = "../crates/bitmap" }` dep + a `[[bin]]` per target), `.github/workflows/coverage-fuzz.yml` (`TARGETS` array + `N_TARGETS` 27→29), `tools/ci/fuzz-tier.sh` (`FAST_TIER`). VERIFY: `make fuzz-build FUZZ_TARGET=fuzz_bitmap_parse` + `fuzz_bitmap_planners` build; `make fuzz-run FUZZ_TARGET=<each> FUZZ_DURATION=120` — must run clean (no crash artifact). If a panic is found, MINIMISE it and FIX the parser/crate (with a phase-1/3 inline regression test); report it. |
| 9b | high | opus | none | Differential `op_bitmap`. In `scripts/differential-fuzz.py`: add `'bitmap'` to `OPERATIONS`; write `_bitmap_option_picker(rng)` (Open question 3 — valid, parity-respecting sequences) + `op_bitmap(instar_bin, instar_copy, qemu_copy, fmt, timeout, rng)` (model `op_amend` `:1723`: create two v3 qcow2 starts with random cluster size, apply the op sequence to each via `run_instar(['bitmap'],…)` / `run_qemu_img(['bitmap'],…)`, compare after each invocation: `compare_exit_codes`, the bitmaps-array from `qemu-img info --output=json` via a new `_normalise_bitmaps_info` helper, and `_qemu_check_metrics`+`_is_clean`); add the `elif op == 'bitmap':` dispatch arm. VERIFY: `python3 scripts/differential-fuzz.py --instar ./src/target/release/instar --ops bitmap --iterations 100 --timeout 60 --log-dir /tmp/bmfuzz` runs with **no unexpected divergence**. If a divergence is found: if it's a real instar bug, FIX it (guest/host, with a regression test) and re-run; if it's an intentional difference the picker should avoid, tighten the picker and record it. Report the run summary + any finding. |
| 9c | medium | opus | none | Verify + docs. `make instar`, `make lint`, `make test-rust` (unaffected), `make fuzz-build` (both targets), a final short `make fuzz-run` per target + a `--ops bitmap` differential burst — all clean; `pre-commit run --all-files`. Mark the phase-9 row complete in `docs/plans/PLAN-bitmap.md` + `index.md`; record any divergence found (in the master plan's Defects/divergence section) and confirm the nightly registration (coverage TARGETS/N_TARGETS/fast-tier + `OPERATIONS`) is complete. Present the commit(s). |

## Management session review checklist

- [ ] Both coverage targets build and run clean (no crash
      artifact); the parser target feeds raw bytes, the planner
      target synthesises plausible directory/refblocks/geometry.
- [ ] Coverage registration complete: Cargo.toml `[[bin]]`s,
      `coverage-fuzz.yml` `TARGETS` + `N_TARGETS`, `fuzz-tier.sh`
      `FAST_TIER`.
- [ ] `op_bitmap` generates **valid, parity-respecting** sequences
      (no `-b`, no mixed merge, 16-bit refcount, valid granularity,
      existing merge source); compares info-bitmaps + check.
- [ ] `'bitmap'` in `OPERATIONS` + the dispatch arm; nightly needs
      no workflow edit (runs all ops with `/dev/kvm`).
- [ ] Any panic/divergence found is **fixed in the product** with a
      regression test, or steered-around-and-registered if
      intentional — reported to the management session.
- [ ] `make instar`/`make lint`/`make test-rust`/`make fuzz-build`/
      the differential burst/`pre-commit run --all-files` green.
- [ ] Commit messages follow conventions.

## Success criteria

- `fuzz_bitmap_parse` + `fuzz_bitmap_planners` exist, build, run
  clean locally, and are registered in the coverage nightly
  (TARGETS/N_TARGETS/fast-tier).
- `op_bitmap` + `_bitmap_option_picker` differentially fuzz instar
  vs `qemu-img bitmap`, registered in `OPERATIONS`, running clean
  over a local burst with any intentional divergence steered around
  and recorded.
- Any product bug the fuzzers found is fixed with a regression test.
- All gates green.

## Back brief

Before executing any step, the executing agent should back brief the
operator — in particular that this phase **validates the panic-free
discipline** the parsers/planner were written for (coverage targets)
and adds the **end-to-end differential net** vs `qemu-img` at scale,
that the differential picker must generate **parity-respecting**
sequences (no `-b`, no mixed merge, 16-bit refcount, valid
granularity, existing merge source), that `op_bitmap` compares
**info-bitmaps + check** (bit content is Phase 7's oracle), and that
any panic/divergence is a **product bug to fix**, not a fuzzer to
weaken.
