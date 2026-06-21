# PLAN-amend phase 05: Rust round-trip tests

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read the existing
planner-crate `tests/` suites (`src/crates/{resize,create,rebase}/tests/`),
the `create` crate's public API, the `qcow2` crate's `QcowHeader` /
`parse_header_extensions` / `header_extension_area_end` / offset and
feature constants, and the `amend` crate's public surface
(`plan_amend_qcow2`, `Qcow2AmendOpts`, `AmendPlan`, `AmendPatch`,
`AmendAction`, `AmendError`). Ground every claim in what the code
actually does — read it, don't guess. Where a question touches the
qcow2 on-disk layout (v2 vs v3 fixed header, header extensions,
`refcount_order`, the backing-file string), confirm against the
header parser. Flag uncertainty rather than guessing.

Phase plans live in `docs/plans/` named
`PLAN-amend-phase-NN-<descriptive>.md`. The master plan is
[PLAN-amend.md](/components/instar/plans/PLAN-amend/); phases 1–4 (ABI, planner, guest op,
host CLI) are landed and `instar amend` runs end-to-end. This is
the fifth of nine.

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained: it should build,
pass tests, and have a clear commit message explaining what changed
and why.

## Situation

Phase 2 gave the `amend` planner inline `#[cfg(test)]` unit tests
that assert on the *emitted patch bytes* for every refusal, the
no-op, the lazy toggle, and the version-change rebuild. Phase 4's
end-to-end smoke test then exercised the whole chain against real
`qemu-img` images and confirmed correctness (and caught two runtime
bugs).

This phase adds the **`src/crates/amend/tests/` integration suite**:
the exhaustive *apply-then-reparse round-trip* matrix the master
plan scopes for phase 5 ("amend → re-parse, assert header
invariants for each transition"). Where phase 2 asserts "the
planner emits these bytes," phase 5 asserts "after applying those
bytes and re-parsing, the resulting qcow2 header is correct and
self-consistent" — and that compound operations (down-then-up,
lazy on-then-off, amend-to-current) preserve the right invariants.
These are pure-Rust host tests (no `qemu-img`, no `/dev/kvm`); the
`qemu-img` cross-checks live in phases 6–7.

If a round-trip assertion fails, that most likely indicates a real
**planner** bug (phase 2) to fix in `src/crates/amend/src/qcow2.rs`,
not a test bug — exactly the kind of edge case this matrix exists
to surface (cf. the lazy-downgrade bug a review caught in phase 2).

The grounding the implementer builds on (verified on the `amend`
branch — re-confirm exact names/signatures while reading, the
report that seeded this plan had minor transcription slips):

- **`tests/` conventions.** `src/crates/resize/tests/` and
  `create/tests/round_trip.rs` are the templates. Integration test
  files under `tests/` compile as separate **std** crates that
  depend on the lib (so `Vec`/`std` are fine, unlike the `no_std`
  lib). A `tests/common/mod.rs` holds shared helpers. `cargo test
  -p amend` (and `make test-rust`'s `cargo test --workspace`) runs
  them automatically — the `amend` crate is a workspace member, not
  excluded.
- **Building v3 fixtures.** `resize/tests/qcow2_grow.rs`'s
  `build_starting_image` calls the `create` crate's `plan_qcow2`
  with a `Qcow2CreateOpts { virtual_size, cluster_size,
  refcount_bits, extended_l2, lazy_refcounts, compat_v3, backing,
  preallocation }`, then `materialise`s the plan's writes into a
  `Vec<u8>`, then `QcowHeader::parse`s it. Reuse this for v3
  fixtures (with/without `lazy_refcounts`, with/without a
  `backing` ref). **Verify the exact `create` API names/fields and
  the `materialise` helper by reading `resize/tests/` — do not
  trust names quoted second-hand.**
- **Building v2 fixtures.** `create`/`build_header` emit **v3 only**
  (version hardcoded to 3). So v2 starting images must be
  **hand-crafted** in a test helper (a `make_v2_header(cluster_size,
  virtual_size, …) -> Vec<u8>` mirroring phase 2's `make_header`,
  setting version=2, `cluster_bits`, `virtual_size`, an L1/refcount
  layout with `refcount_table_offset == cluster_size` and
  `l1_table_offset >= cluster_size` so the guest's layout guard and
  the planner are exercised on realistic offsets), plus an optional
  helper that appends a **backing-format extension (`0xE2792ACA`)
  at offset 72 followed by `EXT_END` and a backing-file string** —
  the exact layout real qemu produces for a v2 image with a backing
  file (confirmed by phase-2 fixture work). (If
  `create::plan_qcow2(compat_v3: false)` turns out to actually emit
  a version-2 header, it may be used for the *no-extension* v2 case
  — check — but hand-crafting is required for the ext-at-72 case
  regardless, and gives byte-exact control.)
- **Apply helper.** Mirror `resize/tests/`'s `apply_resize`: an
  `apply_amend(file: &mut Vec<u8>, plan: &AmendPlan)` that loops
  `plan.patches()` and, for each `AmendPatch::Write { byte_offset,
  bytes }`, copies `bytes` to `file[byte_offset..]` (resizing if
  needed). amend has only the `Write` variant.
- **Re-parse + assert.** After `apply_amend`, `QcowHeader::parse`
  the buffer and assert fields. To confirm extensions survived a
  relocation, use `qcow2::parse_header_extensions(&bytes, &parsed)`
  (returns the backing format etc.) and/or
  `qcow2::header_extension_area_end`.
- **Cargo wiring.** Add a `[dev-dependencies]` block to
  `src/crates/amend/Cargo.toml` mirroring `rebase/Cargo.toml`:
  `create = { path = "../create" }`, `qcow2 = { path = "../qcow2",
  features = ["create"], default-features = false }`, `shared = {
  path = "../../shared" }`. (The non-dev `qcow2` dep already enables
  `create`, but the dev-deps make `create::*` reachable from the
  test crates and are the established pattern.)
- **Public surface.** Tests use `amend::{plan_amend_qcow2,
  Qcow2AmendOpts, AmendPlan, AmendPatch, AmendAction, AmendError}`
  and `qcow2::{QcowHeader, parse_header_extensions,
  header_extension_area_end, VERSION_OFFSET (=4),
  COMPATIBLE_FEATURES_OFFSET (=80), HEADER_LENGTH_OFFSET (=100),
  REFCOUNT_ORDER_OFFSET (=96), COMPAT_LAZY_REFCOUNTS, INCOMPAT_*,
  EXT_BACKING_FORMAT, BackingFormat}`.

## Mission and problem statement

After this phase, `src/crates/amend/tests/` exhaustively round-trips
the planner. For each case: build a header buffer → `plan_amend_qcow2`
→ `apply_amend` → `QcowHeader::parse` → assert. Coverage:

1. **Lazy toggle (same version), round-trip.** v3 image: amend
   `lazy_refcounts=on` → re-parse shows `lazy_refcounts == true`,
   version unchanged, every other header field byte-identical except
   `compatible_features`; then `lazy_refcounts=off` → back to the
   original bytes. Assert the off→on→off cycle returns the exact
   original header cluster.

2. **No-op / idempotency.** Amend a v3 image to its current state
   (`compat=1.1` on a v3, `lazy_refcounts=off` on a non-lazy image)
   → `AmendAction::NoOp`, zero patches, buffer byte-unchanged.

3. **Upgrade v2 → v3.**
   - No extensions: hand-crafted v2 → amend `compat=1.1` →
     re-parse: version 3, `header_length == 104`, `refcount_bits`
     preserved (16 ⇒ `refcount_order` 4), `incompatible_features ==
     0`, `compatible_features == 0` (or lazy bit if also set),
     `virtual_size`/`cluster_size` preserved.
   - With backing-format ext + backing string at offset 72:
     amend `compat=1.1` → re-parse + `parse_header_extensions`
     confirm the backing format survived and now lives at the v3
     boundary (≥104), `backing_file_offset` bumped by +32, the
     backing-file string bytes intact.
   - With `lazy_refcounts=on` in the same amend: `compatible_features`
     has `COMPAT_LAZY_REFCOUNTS` set.

4. **Downgrade v3 → v2.**
   - No extensions: create-built v3 → amend `compat=0.10` →
     re-parse: version 2, `lazy_refcounts == false`,
     `virtual_size`/`cluster_size`/`refcount_bits(16)` preserved,
     the freed v3 fixed-header region (72..`header_length`) no
     longer interpreted as feature words.
   - With a v3 extension (e.g. a feature-name-table-style ext at
     `header_length`, or a backing-format ext + backing string):
     amend `compat=0.10` → re-parse: extensions relocated to offset
     72, `backing_file_offset` bumped by the negative delta, the
     bytes beyond the relocated tail zeroed.

5. **Compound round-trips.**
   - **v2 → v3 → v2** (start hand-crafted v2 with a backing ext):
     upgrade then downgrade returns a v2 whose
     version/virtual_size/cluster_size/refcount_bits and backing
     reference match the original (note any benign byte differences
     and assert on the *parsed* invariants, not raw bytes, across a
     two-way conversion).
   - **v3 → v2 → v3** (start create-built v3, no v3-only features):
     downgrade then upgrade recovers a v3 with the original
     structural fields; lazy is `false` throughout (a v3→v2 step
     clears it — assert that, don't expect lazy to survive).

6. **A few refusal assertions at the suite level** (lighter — the
   exhaustive refusal matrix is phase 2's): downgrade of a v3 image
   with an incompatible-feature bit set → `Err(DowngradeBlockedFeature)`;
   downgrade of a v3 with `refcount_bits != 16` →
   `Err(DowngradeRefcountWidth)`; `lazy_refcounts=on` against a v2
   target → `Err(LazyRequiresV3)`; amend of a `DIRTY` image →
   `Err(Dirty)`. These assert the planner refuses (no apply).

`make test-rust` runs the new suite; `make instar` and `core.bin`
are unaffected (no guest/host code changes — tests only, plus the
`[dev-dependencies]` block).

## Open questions

### 1. v2 fixture construction (confirm before 5a)

Recommendation: **hand-craft v2 headers** in a test helper for full
byte-level control and to exactly reproduce qemu's "ext at offset
72" layout (the upgrade-relocation case can't be built any other
way, since `create` emits v3). The helper mirrors phase 2's
`make_header`. Check whether `create::plan_qcow2` with
`compat_v3: false` emits a true version-2 header; if so it's a fine
*alternative* for the no-extension v2 case, but the hand-crafted
builder is needed regardless. Confirm the approach.

### 2. How strict should compound round-trip assertions be?

A two-way conversion (v2→v3→v2) is **not** guaranteed to be
byte-identical to the original (e.g. instar writes `header_length=
104` and omits the feature-name-table ext on upgrade — the
documented phase-4 divergence; extension ordering/padding may
shift). Recommendation: assert on **parsed `QcowHeader` invariants**
(version, virtual_size, cluster_size, refcount_bits, backing
reference, feature bits) and on extension *survival* via
`parse_header_extensions`, **not** on raw-byte equality, for any
cross-version round-trip. Reserve exact-byte equality for the
*same-version* lazy on→off→on cycle (which must restore the
original cluster exactly). Confirm.

### 3. Does this phase belong to `cargo test -p amend` only, or also `make test-rust`?

`make test-rust` runs `cargo test --workspace` (amend is a member,
not in the `--exclude` list), so the suite runs there automatically
— no Makefile change. Confirm by checking the `--exclude` list does
not contain bare `amend` (it excludes the guest-op crate `amend-op`,
which is a different package).

### 4. Surfacing a planner bug

If any round-trip fails, treat it as a likely **planner** bug and
fix it in `src/crates/amend/src/qcow2.rs` (with a matching phase-2
inline unit test added), rather than weakening the assertion.
Confirm this is the intended posture (it is — the matrix exists to
find such bugs).

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | medium | sonnet | none | Wire up the suite + helpers + the simplest round-trips. Add a `[dev-dependencies]` block to `src/crates/amend/Cargo.toml` (`create`, `qcow2` with `features=["create"] default-features=false`, `shared`) mirroring `src/crates/rebase/Cargo.toml`. Create `src/crates/amend/tests/common/mod.rs` with: `apply_amend(file: &mut Vec<u8>, plan: &AmendPlan)` (loop `plan.patches()`, copy each `AmendPatch::Write` bytes at its offset, resize if needed); `build_v3(virtual_size, cluster_size, lazy, backing: Option<&[u8]>) -> Vec<u8>` using the `create` crate exactly as `resize/tests/qcow2_grow.rs::build_starting_image` does (READ that file for the real `Qcow2CreateOpts`/`plan_qcow2`/`materialise` names); `make_v2_header(cluster_size, virtual_size) -> Vec<u8>` hand-crafting a minimal valid v2 header cluster (version=2, cluster_bits, virtual_size, refcount_table_offset=cluster_size, l1_table_offset=2*cluster_size, l1_size=1) zero-padded to cluster_size; and `put_backing_format_ext(buf: &mut [u8], at: usize, backing_name: &[u8]) -> usize` that writes a `0xE2792ACA` ext (+"qcow2"), an `EXT_END`, and the backing string, updating `backing_file_offset`/`backing_file_size` — returning the meaningful end. Then create `tests/qcow2_round_trip.rs` (`mod common;`) with the lazy on→off→on exact-byte round-trip (case 1), the no-op/idempotency cases (case 2), and the upgrade-no-ext and downgrade-no-ext cases (cases 3a, 4a). Validate with `pre-commit run --all-files` and `make test-rust` (do NOT run cargo directly — sandbox-denied). |
| 5b | medium | sonnet | none | Add the extension/backing and compound cases to `tests/qcow2_round_trip.rs`: upgrade v2→v3 with a backing-format ext at offset 72 (assert ext relocated to ≥104, `backing_file_offset` bumped +32, backing string intact, `parse_header_extensions` finds the backing format) and with `lazy_refcounts=on` (case 3b/3c); downgrade v3→v2 with an extension + backing (assert relocation to 72, offset bumped by negative delta, freed tail zeroed) (case 4b); the v2→v3→v2 and v3→v2→v3 compound round-trips asserting on **parsed invariants** (not raw bytes — see Open question 2), with lazy cleared across a v3→v2 step; and the suite-level refusal assertions (case 6: `DowngradeBlockedFeature`, `DowngradeRefcountWidth`, `LazyRequiresV3`, `Dirty`). If any round-trip exposes a planner bug, STOP and report it to the management session (it is fixed in `src/crates/amend/src/qcow2.rs` with a phase-2 inline test, not by weakening the assertion). Validate with `pre-commit` and `make test-rust`. |
| 5c | low | sonnet | none | Update `docs/plans/PLAN-amend.md`: mark the phase-5 row status. Do NOT add this phase file to `order.yml`; do NOT touch `usage.md`/`CHANGELOG` (phase 9). |
| 5d | low | sonnet | none | From the worktree root: `pre-commit run --all-files`; `make instar` (expect `core.bin`/`amend.bin` unchanged — tests-only phase); `make test-rust` (all suites incl. the new `amend` integration tests). Stage and present a single commit for steps 5a–5c with the CLAUDE.md message convention. Do not push. |

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. After each step the management session reads the
actual changed files, confirms no unrelated files changed, runs the
named gates, and then commits/retries/upgrades. The sandbox **denies
direct `cargo`**; validate via `make test-rust`, `make instar`,
`pre-commit run --all-files`. The management review should read the
new tests and confirm the assertions actually check the invariants
claimed (a test that builds and "passes" but asserts nothing
meaningful is worse than no test).

### Model and effort notes

- 5a and 5b are test authoring against well-established patterns;
  sonnet at medium effort suffices given the helper shapes and the
  reference files named in the briefs. The byte-exact relocation
  assertions in 5b are fiddly but mechanical once the offsets
  (72 ⇔ 104, ±delta) are written down — the management review
  double-checks them.
- 5c, 5d are mechanical.
- When in doubt, skew to the more capable model — but a failing test
  is self-announcing, so test bugs are low-risk to land and fix.

### Management session review checklist

After the steps:

- [ ] Read the new tests — confirm each asserts the invariant it
      claims (not a vacuous pass), especially the relocation offset
      and `parse_header_extensions` survival checks.
- [ ] No unrelated files modified; only `amend/Cargo.toml`
      (dev-deps), `amend/tests/*`, and the master-plan status row.
- [ ] `make test-rust` passes incl. the new `amend` integration
      tests (and the count of amend tests went up meaningfully).
- [ ] `make instar` builds; `core.bin`/`amend.bin` unchanged.
- [ ] `pre-commit run --all-files` clean.
- [ ] Any planner bug a round-trip exposed was fixed in the planner
      (phase 2) with an inline unit test, not papered over.

## Administration and logistics

### Success criteria

Phase 5 is complete when:

* `src/crates/amend/tests/` round-trips every transition (lazy
  toggle, no-op, upgrade ±ext/backing/lazy, downgrade ±ext/backing)
  and the compound (v2→v3→v2, v3→v2→v3) cases, asserting parsed
  header invariants + extension survival, plus suite-level refusal
  assertions.
* The same-version lazy on→off→on cycle restores the exact original
  header cluster; cross-version round-trips assert parsed invariants.
* `make test-rust` runs and passes the new suite; `make instar` and
  the binaries are unaffected; `make lint`/`pre-commit` clean.
* Any planner bug surfaced by the matrix is fixed in
  `src/crates/amend/` with a matching inline unit test.

### Future work created by this phase

- Phase 6: Python integration tests (`tests/test_amend.py`) cross-
  checking against `qemu-img amend` with post-op `info`/`check`/
  `compare`, seeded with the phase-4 `KNOWN_AMEND_DIVERGENCES`
  (instar's `header_length=104` + no feature-name-table on upgrade).
- Phase 7: cross-version baselines.
- If phase 5 surfaces a planner bug, note it in the master plan's
  "Bugs fixed during this work" section.

### Documentation index maintenance

This is a phase plan, not a master plan. It is **not** added to
`docs/plans/order.yml`. The master plan links to it from its
Execution table (already present).

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
