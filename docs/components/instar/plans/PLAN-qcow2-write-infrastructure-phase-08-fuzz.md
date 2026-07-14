# qcow2 write infrastructure — phase 08: fuzzing

Parent:
[PLAN-qcow2-write-infrastructure.md](/components/instar/plans/PLAN-qcow2-write-infrastructure/).
Planned at **medium effort** (the master plan's tier for phases 2/8/9 —
this follows a well-established fuzz-target pattern, not a novel design).
Adds fuzz coverage for the qcow2-write work along the two threads the
master plan names:

1. **A coverage-guided fuzz target for the new crate** (`qcow2-write`) —
   a libFuzzer target that drives the write/flush/COW/growth planner and
   asserts its invariants (no panic, no rc≥3, snapshot-cluster
   preservation, refcount + COPIED consistency), plugging into the
   repo's existing cargo-fuzz apparatus.
2. **Differential coverage for the newly-permitted snapshot-bearing
   writes** — extend `scripts/differential-fuzz.py` to generate
   snapshot-bearing commit / rebase / bench fixtures and apply the
   phase-7 snapshot read-back oracle, so the COW paths phase 7 opened
   are continuously differential-tested against qemu.

Grounded in a fresh survey (2026-07-14) of the cargo-fuzz harness
(`src/fuzz/`), the differential fuzzer (`scripts/differential-fuzz.py`),
the crate simulation harness, and the CI wiring.

## What is different about phase 8 (and why it's cheaper than 4-7)

Phase 8 adds **verification infrastructure**, not behaviour — no
`src/operations/*` or crate *semantics* change. The tooling is already
decided by precedent (see the grounding), so there is no design
question to settle; the work is (a) exposing the existing simulation
harness to a new fuzz binary, (b) writing that binary's decode +
invariant oracle, and (c) teaching the differential fuzzer to build
snapshot-bearing fixtures it currently avoids by construction. The
proof is "it builds, runs, and finds zero *real* crashes/divergences on
a bounded run," and it wires into the two existing nightly fuzz
workflows.

## Grounding: what already exists (do not rebuild)

* **cargo-fuzz is the repo's fuzzing tool.** `src/fuzz/` is a cargo-fuzz
  project (`libfuzzer-sys` 0.4, `cargo-fuzz = true`), `exclude`d from the
  `src/` workspace, with 30 `[[bin]]` targets — including *planner*
  targets (`fuzz_commit_planners`, `fuzz_resize_planners`, …) that are
  the direct precedent. cargo-fuzz + nightly are baked into the
  `instar-build` devcontainer; `make fuzz-build` / `make fuzz-run
  FUZZ_TARGET=… FUZZ_DURATION=…` (Makefile ~:523/:539) run it in Docker.
  There is NO proptest/arbitrary/afl anywhere. **qcow2-write is not yet a
  fuzz dependency** — thread 1 is greenfield but plugs into this pattern.
* **The simulation harness exists but is `#[cfg(test)]`-gated.** The
  Vec-backed `TestImg` + `exec` (the executor role) + `run_write` /
  `run_flush` (the plan/execute/`BufFull`-resume loop) + the COW fixtures
  (`mk_cow`, `add_shared_data`, `add_shared_l2_two_children`) + the
  assertion helpers (`rc_of`, `max_rc` — "the corruption signature COW
  must NEVER produce is any rc ≥ 3" — `disk_u64`) live inside
  `#[cfg(test)] mod tests` in `src/crates/qcow2-write/src/lib.rs`. A fuzz
  binary cannot reach them; **lifting them into a reusable feature-gated
  `pub` module is thread 1's one real prerequisite.** (A second, separate
  harness lives in `crates/qcow2-write/tests/simulation.rs` /
  `ordering.rs` — integration-test only; leave it, or optionally converge
  later.)
* **The differential fuzzer avoids snapshots by construction.**
  `scripts/differential-fuzz.py`'s commit / rebase / bench pickers
  (`_commit_option_picker`, `_rebase_option_picker`,
  `_bench_option_picker` / `_build_bench_image`) build plain
  backing/overlay/fresh fixtures and never emit `qemu-img snapshot -c`;
  the bench picker's own comment says "no snapshots/compression". The
  `op_snapshot` op has a documented "divergence-avoidance table" — that
  op is unrelated (it exercises the snapshot subcommand chain, not COW
  writes). So instar's write ops are only differential-tested against
  snapshot-*free* images: exactly the gap phase 7's COW opened and
  phase 8 must close.
* **The read-back oracle is ready to reuse.**
  `tests/helpers/snapshot_readback.py` — `snapshot_readback(qemu_img,
  image, snapshot) -> sha256` (apply-on-a-copy → convert → sha256, per-op
  expected value). `scripts/cow-soak.py` (phase 7e) already has the
  snapshot-bearing generators (`gen_commit` / `gen_rebase` / `gen_bench`,
  backing-vs-overlay shapes, snapshot-shared bench span) and the full
  C5-C8 parity (`assert_parity`: compare + check + per-snapshot
  read-back). cow-soak.py is standalone, not wired to CI — its generators
  and oracle are the near-drop-in template for the fuzzer's
  snapshot-bearing branch.
* **CI has both fuzz workflows.** `coverage-fuzz.yml` (nightly, self-hosted
  xl, Docker) builds `src/fuzz` and runs each target `cargo fuzz run …
  -max_total_time=…` with crash tmin + auto-issue; a new target registers
  in three places: the `[[bin]]` in `src/fuzz/Cargo.toml`, the `TARGETS`
  array in `coverage-fuzz.yml`, and the `FAST_TIER` list in
  `tools/ci/fuzz-tier.sh` (a planner target belongs in the fast tier —
  the script's comment names planner/emitter crates as the archetype).
  `differential-fuzz.yml` (nightly 1000 iters / PR 100 / push-to-develop
  200, Docker + `/dev/kvm`, `--create-issues`) runs the differential
  fuzzer — the snapshot extension rides it with no new CI.

## Scope

Deliverables: the crate fuzz target + harness lift (8a), the
snapshot-bearing differential coverage (8b), and the docs close-out (8c);
each preceded by a bounded local run that must be crash/divergence-free.
Out of scope: any change to op or crate *semantics* (a real bug found
here is a STOP that routes back to the owning phase, not a patch in
phase 8); the second integration-test simulation harness; new fuzzing
technology (cargo-fuzz is fixed by precedent).

### Settled design decisions (binding)

1. **Tooling: cargo-fuzz + libFuzzer, by precedent.** Thread 1 adds one
   or two `[[bin]]` targets to `src/fuzz` depending on `qcow2-write`
   (path dep), byte-decoded input, panic/`assert!` oracle, run via the
   existing `make fuzz-*` + `coverage-fuzz.yml` fast tier. No proptest.
2. **Expose the sim harness via a feature-gated `pub` module.** Move
   `TestImg` + `exec` + `run_write` / `run_flush` + the COW fixtures +
   the assertion helpers out of `#[cfg(test)] mod tests` into a
   `#[cfg(any(test, feature = "sim"))] pub mod sim` (name at 8a's
   discretion — `sim`/`testkit`). The crate's existing unit tests import
   it from there and must stay green unchanged (this is the correctness
   proof of the lift — a pure move, like the phase-6 growth-planner
   move). The `sim` feature is off in the production build (no bloat);
   the fuzz crate enables it. No behaviour change.
3. **The crate fuzz target's decode + oracle.** Fuzz bytes choose (a) a
   fixture archetype — clean / backing-present / shared-data (C1) /
   shared-L2 (C2) / nested (C3) / zero-flag-target / a spread of cluster
   sizes {512, 4096, 65536, 2 MiB} and virtual sizes — and (b) a bounded
   sequence of operations: `plan_write(voff, len, DataSource)` and
   `plan_flush`, driven through the `BufFull`-resume loop. After every
   successful plan+exec the target asserts the harness invariants (the
   oracle): **no panic / no arithmetic overflow** (libFuzzer + the
   planner's own `OutOfBounds`/checked math); **max_rc < 3** (the COW
   corruption signature); **snapshot-shared clusters byte-preserved and
   never freed** (rc stays ≥ 1); **refcount self-consistency** (every
   L1/L2-referenced cluster has rc ≥ 1, no dangling/past-EOF pointer);
   **COPIED-flag correctness** on patched entries; **plan/flush
   convergence** (the resume loop terminates under the iteration cap). A
   `WriteError` refusal is a valid outcome, not a crash — but a refusal
   must leave the staged image self-consistent (no half-applied
   mutation). Optionally a second small target fuzzes
   `plan_refcount_growth` geometry for overflow/self-coverage-invariant
   (cheap; it is pure arithmetic).
4. **Snapshot-bearing differential coverage extends the existing
   pickers.** Add a snapshot-bearing branch to `op_commit` / `op_rebase`
   / `op_bench` (probability-weighted, e.g. ~40% of those ops), lifting
   cow-soak's `gen_*` fixture recipes (backing-vs-overlay snapshot
   placement for commit; snapshot-bearing overlay for safe rebase;
   snapshot-shared write span for bench). When a fixture carries
   snapshots, the oracle GAINS the read-back triple: after the op,
   `qemu-img compare` the active view + `qemu-img check` clean (explicit
   `refcount=1 reference=2` scan) + `snapshot_readback` every carrier
   snapshot instar-vs-qemu-twin — reusing `tests/helpers/snapshot_readback.py`
   (import it the way cow-soak does). Non-snapshot fixtures keep today's
   exit-code + info-JSON / compare+check oracle unchanged. The
   `-u`-rebase and non-COW paths are untouched.
5. **Fold and retire cow-soak.py.** Its generators + `assert_parity`
   move into the differential fuzzer (thread 2). Once the snapshot
   branch lands and is proven, delete `scripts/cow-soak.py` (nothing
   depends on it; the cross-version test `tests/test_cow_cross_version.py`
   stays — it is a different, version-matrix artifact). If a standalone
   quick soak is still wanted, `differential-fuzz.py --ops commit,rebase,bench`
   with a seed serves it.
6. **Determinism.** New RNG draws inside the pickers shift that op's
   stream but stay deterministic under a fixed top `--seed`; there are no
   golden-seed expectations to regenerate (the fuzzer compares against
   qemu live, not against a frozen corpus). Adopt cow-soak's namespaced
   per-iteration seed style **only if** clean single-iteration `--replay`
   is wanted for the snapshot branch; otherwise the existing `iter_seed`
   recording suffices. 8b picks the lighter option that preserves
   replayability of a reported divergence.
7. **A real bug found by either thread is a STOP, not a phase-8 fix.** If
   the crate target finds a planner panic / invariant violation, or the
   differential branch finds a COW divergence vs qemu, STOP and report
   with the minimized input / iter_seed + decoded evidence — it routes
   back to 7a (crate) or 7b/7c/7d (op), because phase 8 is verification,
   not a place to change semantics. (Classify a differential divergence
   as real-vs-flake by isolated same-seed replay first — the
   contention-flakiness lesson.)

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 8a | high | default | none | Thread 1: lift the sim harness + add the crate fuzz target. (i) Move `TestImg` + `exec` + `run_write`/`run_write_ok`/`run_write_err` + `run_flush`/`run_flush_ok` + the COW fixtures (`mk`/`mk_vs`/`mk_cow`, `add_shared_data`/`add_shared_l2_two_children`/`add_owned_l2`/`add_allocated_cluster`) + the helpers (`rc_of`/`max_rc`/`disk_u64`/`set_rc`/`set_l1`/`put_disk_u64`/`kinds`/`caller_data`) out of `#[cfg(test)] mod tests` in `src/crates/qcow2-write/src/lib.rs` into `#[cfg(any(test, feature = "sim"))] pub mod sim`; add the `sim` feature to the crate Cargo.toml; update the unit tests to import from the new module. PROVE the lift is a pure move: `make test-rust` green unchanged (the same tests run against the relocated harness). (ii) Add `qcow2-write = { path = "../crates/qcow2-write", features = ["sim"] }` and a `fuzz_qcow2_write` `[[bin]]` to `src/fuzz`; write the target per decision 3 (decode archetype + write/flush sequence from bytes; assert the invariant oracle). Optionally a `fuzz_qcow2_write_growth` target. (iii) Register in `coverage-fuzz.yml` `TARGETS` + `tools/ci/fuzz-tier.sh` `FAST_TIER`. Build (`make fuzz-build`) and run a bounded shake-out (`make fuzz-run FUZZ_TARGET=fuzz_qcow2_write FUZZ_DURATION=300`) — ZERO crashes; seed the corpus. make test-rust / lint / sizes (the crate change must not affect the ops' `.bin` — `sim` is off in prod) / pre-commit. STOP + report if the target finds a real planner crash (routes back to 7a). |
| 8b | high | default | none | Thread 2: snapshot-bearing differential coverage. Extend `scripts/differential-fuzz.py`'s `op_commit`/`op_rebase`/`op_bench` with a probability-weighted snapshot-bearing fixture branch (lift cow-soak's `gen_commit`/`gen_rebase`/`gen_bench` recipes) and, for snapshot-bearing fixtures, add the read-back oracle to the comparison: active-view `qemu-img compare` + `qemu-img check` clean (with a `refcount=1 reference=2` scan) + per-carrier `snapshot_readback` instar-vs-qemu-twin (import `tests/helpers/snapshot_readback.py`). Non-snapshot fixtures + all other ops UNCHANGED. Preserve determinism + single-iteration replay (decision 6). Fold + delete `scripts/cow-soak.py` (decision 5). Run a bounded local soak — `differential-fuzz.py --instar … --ops commit,rebase,bench --iterations 300 --seed <fixed>` — ZERO divergences (classify any hit real-vs-flake by isolated same-seed replay). Confirm the untouched ops still pass a smoke (`--ops snapshot,convert --iterations 50`). pre-commit. STOP + report a real COW divergence (routes back to 7b/7c/7d) with iter_seed + decoded evidence. |
| 8c | medium | default | none | Docs close-out. ARCHITECTURE.md (the qcow2-write `sim` module + `fuzz_qcow2_write` target; the snapshot-bearing differential coverage; cow-soak folded in); docs/ fuzzing notes (if a fuzzing doc exists, add the new target + the snapshot branch; else a short section); CHANGELOG; master plan execution table row 8 → Complete + a "Findings: phase 8 fuzzing" section (the two threads, the invariant oracle, the read-back-in-fuzz technique, corpus/seed notes, what phase 9 documents) + note the cow-soak retirement; plans index; README/AGENTS only if user-facing. pre-commit. |

Ordering: 8a and 8b are independent (crate target vs python fuzzer) and
MAY run concurrently; 8c last. One commit per step minimum; management
reviews and commits. Each of 8a/8b lands only with a clean bounded run.

## Review checklist deltas

* **The harness lift (8a) is a pure move** — a reviewer confirms the
  crate's existing unit tests are unchanged in behaviour and green, and
  that the `sim` feature is OFF in the production build (the ops' `.bin`
  sizes are unchanged; no `sim` code links into the guest binaries).
* **The crate fuzz target's oracle is the harness invariants** — most
  importantly `max_rc < 3` (the COW corruption signature) and
  snapshot-cluster byte-preservation; a reviewer confirms a `WriteError`
  refusal is treated as a valid outcome, not a crash, and that the
  target seeds a corpus.
* **The differential snapshot branch reuses the shared oracle** —
  `tests/helpers/snapshot_readback.py`, not a reimplementation; the
  per-op expected value is correct (commit preserved / rebase +
  overlay-post-write read-through-new, the "== qemu twin" invariant).
* **Non-snapshot fuzzer behaviour is unchanged** — the existing ops and
  the non-snapshot commit/rebase/bench branches keep their current
  oracle; determinism + divergence replay are preserved.
* **cow-soak.py is deleted, its coverage folded in** — no orphaned
  duplicate; `test_cow_cross_version.py` (the version-matrix artifact)
  stays.
* **A real crash/divergence is a STOP** — phase 8 does not change op or
  crate semantics; a finding routes back to phase 7 with a minimized
  repro.
* **CI registration is complete** — the new target is in
  `src/fuzz/Cargo.toml`, `coverage-fuzz.yml` `TARGETS`, and
  `fuzz-tier.sh` `FAST_TIER`; the snapshot branch needs no new CI (rides
  `differential-fuzz.yml`).
