# PLAN-bench phase 07: fuzz

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (the cargo-fuzz
targets in `src/fuzz/fuzz_targets/`, the nightly tier machinery,
`scripts/differential-fuzz.py`'s op shape), and ground your
answers in what the code actually does today. Do not speculate
when you could read instead; flag uncertainty explicitly.

Phase plans live alongside the master plan
([PLAN-bench.md](/components/instar/plans/PLAN-bench/)) in `docs/plans/`. This is the
seventh of eight phases; phases 1-6 delivered a working,
suite-guarded `instar bench`. This phase adds the two fuzz
surfaces the master plan requires: a coverage target over the
pure schedule crate and a differential op over the deterministic
CLI surface (never durations — OQ13).

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained: it should build,
pass tests, and have a clear commit message explaining what
changed and why.

## Situation

Surveyed grounding:

- **Coverage fuzz**: cargo-fuzz/libfuzzer in the standalone
  `src/fuzz/` crate (excluded from the workspace); 29 targets.
  The archetype for a pure-arithmetic target is
  `fuzz_targets/fuzz_dd_window.rs`: `#![no_main]`, a fixed
  `HEADER_BYTES` prefix decoded by manual little-endian slicing
  (no `Arbitrary` anywhere in the tree), the crate function
  called directly, invariants asserted with `assert!`/
  `assert_eq!` (panic is the oracle). `crates/bench` is
  dependency-free `no_std`, so the fuzz crate needs only
  `bench = { path = "../crates/bench" }` and a `[[bin]]` — no
  features, no `src/fuzz/src/lib.rs` change (no CallTable
  involvement, exactly like dd).
- **Registration is three places**: `src/fuzz/Cargo.toml`
  (`[[bin]]`), `.github/workflows/coverage-fuzz.yml` (the
  hardcoded `TARGETS=(...)` array ~lines 227-257 AND the
  `N_TARGETS=29` default at ~line 156 plus its comment), and
  `tools/ci/fuzz-tier.sh`'s `FAST_TIER=(...)` (~lines 34-47 —
  "tiers" are duration classes: fast-tier targets get a fixed
  300 s slice of the 27000 s nightly budget; pure-math targets
  like `fuzz_dd_window` live there and `fuzz_bench_schedule`
  belongs beside them).
- **No corpus seeding needed**: `scripts/extract-fuzz-corpus.py`
  only seeds image-structured targets; pure-byte targets
  (dd_window, chs_rounded_size) have no entry — libFuzzer
  self-generates and the nightly commits discoveries back to
  instar-testdata `custom/fuzz-corpus/<target>/` on its own.
- **`assert_split_invariants` is private and test-gated**
  (`src/crates/bench/src/lib.rs:797`, inside `#[cfg(test)]`);
  its doc comment promises phase 7 reuses "these exact
  invariants". House convention (every planner target) is that
  invariant helpers live **in the fuzz target file** — so the
  target re-implements the checks verbatim rather than the crate
  exporting them. The crate is untouched by this phase.
- **Differential fuzz**: `scripts/differential-fuzz.py` — ops
  are module functions `op_<name>(...)` registered in the
  `OPERATIONS` list (~line 52, ends `'dd', 'bitmap'`) plus an
  `elif` in `run_iteration`'s dispatch (~line 3831). The newest
  exemplar to clone is `op_bitmap` (~2065-2189) with
  `_bitmap_option_picker` (~1914): pickers **steer around**
  known divergences so any surfaced divergence is real; results
  are typed divergence dicts (or None); either-side timeout
  reclassifies as `inconclusive_external_timeout`;
  `--create-issues` files `security-audit` issues. The nightly
  differential workflow runs with `/dev/kvm` in the
  devcontainer.
- **Local mechanics**: `make fuzz-build FUZZ_TARGET=...` /
  `make fuzz-run FUZZ_TARGET=... FUZZ_DURATION=...` (container-
  wrapped; raw cargo denied). Smoke convention: build + a short
  run before registering.

## Mission

### 1. `fuzz_bench_schedule` (coverage target)

`src/fuzz/fuzz_targets/fuzz_bench_schedule.rs`, dd_window-shaped.
Input header (manual LE slicing; reject short inputs):

```
count u32 | depth u32 | bufsize u64 | step u64 | offset u64 |
flush_interval u32 | flags u8 (bit0 is_write, bit1 no_drain) |
pattern u8 | image_size u64
```

The raw values are deliberately UNCLAMPED — the crate must be
panic-free on garbage. Body, in order:

1. Build `BenchParams`; call `validate()` — must never panic;
   when it returns an error, assert the error is consistent with
   a re-check of the violated bound (each `BenchParamError`
   variant maps to a predicate; assert the predicate holds).
   Then, to exercise the schedule machinery on meaningful
   values, derive a CLAMPED copy (count capped to 4096 for
   iteration cost, bufsize into [1, BENCH_MAX_BUFSIZE], step ≤
   QEMU_BENCH_ARG_MAX) and continue with it.
2. `effective_step()`: assert `== bufsize` iff `step == 0`,
   else `== step`.
3. `next_offset` direct probes: for the raw (unclamped) u64s,
   call it and assert only that it returns (panic-freedom) and
   that the result is `< image_size - bufsize` when
   `image_size > bufsize`, `== 0` when `image_size <= bufsize`.
4. `OffsetSchedule::new(&clamped, image_size)`: assert it yields
   exactly `count` items; the FIRST equals the raw
   `params.offset` (unwrapped); every subsequent offset
   satisfies the §3 bound; the iterator's `size_hint` is exact.
5. `TransferSplit::new(offset, len, max)` over fuzz-derived
   values (len capped at 16 MiB for cost): re-implement the
   `assert_split_invariants` checks verbatim (each chunk `>0`,
   `<= max`, contiguous ascending from `offset` with
   saturating advance, zero chunks iff `max == 0`, lengths sum
   to `len`).
6. Flush cadence: assert
   `(1..=count).filter(|k| flush_after_completion(count, *k, i)).count()
   == total_flushes(count, i) as usize` for the clamped count
   and the fuzzed interval (and interval 0 ⇒ both zero); assert
   `flush_after_completion(count, 0, i)` and
   `(count+1)` probes are false.

Doc-comment the target with the invariant list (dd_window
style), crediting `assert_split_invariants` as the source of §5.

### 2. Registration + smoke

The three registration spots (Situation), with
`fuzz_bench_schedule` added to `FAST_TIER` (pure math). No
corpus-extraction entry. Local gate: `make fuzz-build
FUZZ_TARGET=fuzz_bench_schedule` then `make fuzz-run
FUZZ_TARGET=fuzz_bench_schedule FUZZ_DURATION=120` — must
complete with zero crashes (a crash is a stop-and-report: it is
a real crate bug, phase 1's tests notwithstanding).

### 3. `op_bench` (differential)

Clone op_bitmap's shape. `_bench_option_picker(rng)` generates
argument vectors **inside the validated envelope and outside
every KNOWN_BENCH_DIVERGENCES trigger** (steer-around, the house
rule — any surfaced divergence is then real):

- `-c` ∈ [1, 256] (speed), `-d` ∈ [1, 64], `-s` ∈ [1, 2 MiB]
  (biased to sub-64 KiB plus occasional straddlers), `-S` ∈
  {0, powers near bufsize, unaligned odd values},
  `--pattern` ∈ [0, 255].
- `-o` and the wrap window: constrain
  `offset + bufsize <= image_size - bufsize` so the schedule
  never enters the region where qemu 10.0.8's own wrap bug EIOs
  (the `wrap-rule-10-0-8` divergence steered around, not
  filtered after the fact).
- `-w` only for qcow2 and raw images; `--flush-interval` only
  with `-w` and ≥ depth (or 0); `--no-drain` only with a
  nonzero interval. Never `-t`/`-i`/`-n`/`--image-opts` (all
  divergence postures). vmdk/vpc images: read-only vectors.
- Images: self-built per invocation like op_bitmap —
  qcow2 (random cluster size, `compat=1.1`, `refcount_bits=16`,
  no snapshots/compression), raw **with the MBR pattern**
  (the `secure-raw-detection` divergence steered around), vmdk,
  vpc; sizes small (≤ 64 MiB); optionally pre-populate some
  clusters via qemu-io so both fast-path and allocating writes
  get exercised.

Oracle layers per invocation (never durations):

1. `compare_exit_codes` (existing helper; either-side timeout →
   inconclusive).
2. Header parity: first stdout line byte-equal when both exited
   0 (both tools print it before running); on validation
   failures, core stderr text containment both ways for the
   parity messages.
3. For `-w` on shared success: `qemu-img compare` the two images
   (virtual view) + `qemu-img check` structural cleanliness on
   the instar image (reuse `_qemu_check_metrics`/`_is_clean`).
   Raw `-w`: byte equality (sha256) — the stronger oracle.
4. Typed divergence dicts `{'type': 'bench_exit_divergence' |
   'bench_header_divergence' | 'bench_write_divergence' |
   'bench_check_divergence', 'operation': 'bench', ...}` with
   the argv and image recipe embedded for reproduction.

Register: `OPERATIONS` append + `run_iteration` elif. No new
KNOWN_* registry unless the local run surfaces a legitimate
divergence the picker cannot reasonably steer around — in that
case stop-and-report (it belongs in the phase-6 registry too).

### 4. What phase 7 does not do

No timing comparisons of any kind. No fuzzing of the guest op
binary itself (the format parsers it calls are already covered
by the 29 existing targets; bench adds no new parsing). No
corpus seeding. No workflow-cadence changes (the nightly budgets
absorb one more fast-tier target and one more differential op
without retuning).

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | medium | sonnet | none | `fuzz_bench_schedule` per Mission §1 + the three registration spots per §2 (Cargo.toml [[bin]] + dep, workflow TARGETS array + N_TARGETS=30 + comment, FAST_TIER). Local smoke: fuzz-build + fuzz-run 120 s, zero crashes (crash = stop-and-report). actionlint via pre-commit covers the workflow edit. Commit 1. |
| 7b | high | opus | none | `op_bench` + `_bench_option_picker` per Mission §3, cloned from op_bitmap's shape (typed dicts, steer-around picker, existing compare helpers). Local verification: run `scripts/differential-fuzz.py --instar src/target/release/instar --iterations 100 --timeout 30 --log-dir <scratchpad>` (NO --create-issues) on this KVM host; confirm bench invocations occurred (grep the log/summary), ZERO bench divergences reported, and no inconclusive storm (a few external timeouts are tolerable). Any real divergence: stop-and-report with the reproduction seed. Commit 2. |
| 7c | low | sonnet | none | Bookkeeping: append `## Captured fuzz results (step 7c)` here (smoke-run stats, differential-run stats incl. how many iterations exercised bench), master plan row → Complete, index update, pre-commit. Commit 3. |

## Captured fuzz results (step 7c)

**7a smoke.** `fuzz_bench_schedule` was registered as the 30th
coverage-fuzz target (`FAST_TIER`, pure math's fixed 300 s
nightly slice). The local smoke: 1,306,856 runs in 121 s
(~10,800 exec/s), zero crashes, coverage saturating early (cov
49 / ft 71) — expected for a small pure-arithmetic input space
with no format parsing behind it.

**7b local differential run.** `op_bench` (seed 20260708, 100
iterations, 233.8 s): zero bench divergences. 22 iterations
invoked bench across 25 op-slots — close to the ~19 expected
from a 3-ops-per-iteration draw over 16 registered operations —
and the two inconclusives that surfaced belonged to other ops,
not bench. The picker's schedule-linear wrap steer-around
(`offset + (count-1)*eff_step + bufsize < image_size`, keeping
the whole schedule inside the region where neither qemu
10.0.8's wrap rule nor instar's ever fires) was verified over
30,000 generated plans with zero wrap leaks.

Nightly exercise of both surfaces begins with the next
scheduled coverage-fuzz and differential-fuzz runs; any corpus
entries or divergence issues they surface flow through the
existing machinery (instar-testdata corpus commits, security-
audit issue filing) — no phase-7-specific follow-up is needed.
