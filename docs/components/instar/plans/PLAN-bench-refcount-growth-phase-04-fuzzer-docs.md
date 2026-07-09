# bench refcount growth — phase 04: fuzzer retirement + docs

Parent: [PLAN-bench-refcount-growth.md](/components/instar/plans/PLAN-bench-refcount-growth/).
Planned at medium effort.

## Scope

1. **scripts/differential-fuzz.py**: remove the
   `qcow2-write-refblock-coverage` steer-around — the
   `_BENCH_WRITE_SAFE_CLUSTERS` constant, the post-invocation
   cluster-size override in `_bench_option_picker`, and the
   docstring bullet — restoring the full
   `QCOW2_CLUSTER_SIZES` matrix for `-w` recipes. (Removing the
   extra `rng.choice` draw restores the pre-steer RNG stream, so
   the original issue iterations replay exactly.)
2. **docs/bench.md**: drop the `qcow2-write-refblock-coverage`
   known-divergence bullet; the "Write tests" growth text landed
   in phase 2.
3. **CHANGELOG.md**: Unreleased → Added entry describing the
   growth feature and the retired limitation.
4. **docs/plans/index.md + order.yml**: master-plan row and nav
   entry (if not already added when the plan landed); set the
   status to Complete at the end of this phase.
5. **Validation** (management session, not sub-agent):
   * replay iter_seeds 978924777, 2808571592, 2843295063,
     933136565, 3155371439 against the rebuilt binary + updated
     fuzzer — all must report `divergence: null`;
   * a ≥ 250-iteration soak (fresh seed) — 0 divergences;
   * full test suites + pre-commit.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | default (Fable) | none | Apply Scope items 1-4. The steer-around was added in commit ee2cc5d — use it as the reverse map, but keep the KNOWN_BENCH_DIVERGENCES *history* accurate: the docs/bench.md "Known divergences" list simply loses the bullet (registry entry removal is phase 3's). Keep the CHANGELOG entry factual: what grew, the new refusal envelope (needed slots > 2048 / staged bytes > 2 MiB / RT > 64 KiB), the crash-window note, and the fuzzer-matrix restoration. |

## Review checklist deltas

Standard checklist, plus the parent plan's success criteria
(issue replays + soak) executed and recorded before marking the
master plan Complete.
