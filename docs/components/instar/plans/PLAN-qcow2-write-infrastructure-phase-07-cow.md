# qcow2 write infrastructure — phase 07: copy-on-write

Parent:
[PLAN-qcow2-write-infrastructure.md](/components/instar/plans/PLAN-qcow2-write-infrastructure/).
Planned at **high effort** (the master plan mandates it — COW
correctness and the crash-ordering contract are the hardest design
in the whole programme, and this is the first phase to add net-new
behaviour rather than migrate existing behaviour). Adds copy-on-write
to `crates/qcow2-write` and adopts it in **all three** consumers
(commit, rebase safe mode, bench `-w`), lifting their interim
snapshot-refusal gates; fixes the #432 read-path zero-flag defect
fix-first; and wires the shared refcount-growth planner into commit and
rebase so COW is not capped by the no-append ceiling.

Grounded in a fresh survey (2026-07-13) of the three refusal layers,
the crate classification, and the `crates/qcow2` chain reader, plus the
phase-1 empirical qemu semantics (Q1/Q2/Q3) and the phase-6 findings.

## What is different about phase 7 (and why the bar changes)

Phases 4-6 were **refactors**: migrate an existing composition onto the
crate and prove the bytes did not move (before-vs-after migration-proof).
Phase 7 is the opposite in three load-bearing ways that reshape the bar.

1. **COW is net-new behaviour — there is no before/after byte identity.**
   Today every consumer *refuses* a snapshot-bearing image; after phase 7
   it *succeeds by copying*. The pre-migration binary and the post-phase-7
   binary produce different outcomes on the same fixture by design (refuse
   vs succeed), so the phase-4-6 migration-proof harness does not apply to
   the COW surface. The proof is **qemu-parity**, per OQ3 (resolved):
   `qemu-img check` clean + active view `qemu-img compare`-identical to
   qemu's result + **every pre-existing snapshot's virtual view
   bit-identical to qemu's result for that op** (the new snapshot
   read-back oracle). Byte parity with qemu's *placement* is neither
   required nor achievable (qemu's COW placement is nondeterministic at
   512-byte clusters — Q3); instar takes its own layout freedom (the
   shared L2-first cursor) and the oracle constrains content + structure,
   never image bytes.

2. **The snapshot-view semantic differs per op — the oracle's expected
   value is not "unchanged".**
   - **commit** (Q1): qemu COWs the shared clusters and **preserves** every
     internal snapshot bit-identically. Read-back expected value = the
     snapshot's *pre-commit* content.
   - **rebase safe mode** (Q2): qemu's contract covers the **active view
     only**; internal snapshots are left untouched, so a snapshot's
     unallocated ranges silently **read through the NEW backing**
     afterwards. Read-back expected value = the snapshot applied against
     the *new* backing (NOT its pre-rebase content). instar must reproduce
     this read-through-new-backing semantic, not avoid it.
   - **bench** `-w`: writes into the active view of a snapshot-bearing
     image; snapshots preserved (like commit).
   The oracle therefore compares instar's result against **qemu's result
   on the same fixture**, computed per-op, never against a frozen baseline.

3. **The core net-new work is refcount COW machinery, not step plumbing.**
   The step vocabulary (`ReadCluster` into `Bounce`, `WriteRange`,
   `PatchEntryU64`, in-place staged-refblock mutation) is already
   COW-capable — phase 7 adds **no new `StepKind`**. What is net-new is the
   refcount bookkeeping COW requires, which v1 never needed (v1 only ever
   *increments* on allocation):
   - **Data-cluster COW** (`WriteError::SnapshotShared`): allocate `D'`,
     copy `D → D'`, patch the L2 entry `D' | COPIED`, set `refcount(D')=1`,
     **decrement** `refcount(D)` (the snapshot still references it: 2→1).
   - **L2-table COW** (`WriteError::SnapshotSharedL2Table`): allocate `T'`,
     copy `T → T'`, patch the L1 entry `T' | COPIED`, set `refcount(T')=1`,
     **decrement** `refcount(T)`, and **leave the child data clusters'
     refcounts untouched**. (7p CORRECTION — the original plan's
     "increment every child 1→2" was WRONG and would corrupt: qemu bumps
     every reachable data cluster's refcount to ≥2 eagerly at
     *snapshot-creation* time, so by the time an L2 table is COWed its
     children are ALREADY rc≥2 with `OFLAG_COPIED` clear. Copying `T → T'`
     merely redistributes the reference — before, one physical L2 entry in
     `T` points at child `D`; after, `T'[j]→D` and `T[j]→D`, net child
     delta 0 — so the refcount stays correct at its existing value. A
     literal child-increment drives children to rc3 → `qemu-img check`
     dirty → corruption. The children already classify `SnapshotShared`
     (COPIED clear), so writing through `T'` into a child triggers the
     per-child data-cluster COW above, per write. Verified version-
     invariant, zero rc3 clusters, check clean — see "Findings: 7p".)
   Decrement is a new refblock mutation the snapshot allocator does not
   currently expose; refcount coverage can still grow during COW (from the
   NEW `T'`/`D'` allocations, not from child bumps); and the crash-ordering
   contract must place the copied data durable before the pointer flip and
   the refcount changes last, exactly as the existing ordering does for
   allocation.

## Scope

Deliverables: the #432 read-path fix (7z, fix-first), the crate COW
branch + refcount COW machinery + growth-execution sharing (7a), commit
COW adoption (7b), rebase COW adoption (7c), bench COW adoption (7d), the
COW parity harness + snapshot read-back oracle + soak (7e), docs
close-out (7f); preceded by probes (7p). The heavy differential-fuzz
snapshot generation and coverage fuzzing are **phase 8** — phase 7 builds
only the minimal integration-test read-back oracle over the phase-1
hand-built fixtures, which phase 8 scales.

Out of scope: `convert` (structurally a bump allocator, never
consolidated); `amend refcount_bits`; `--preallocation` bulk allocation;
non-qcow2 `bench -w`. The `-u` (unsafe/metadata-only) rebase path stays
exactly as-is and its qemu-parity test stays green.

### Settled design decisions (binding)

1. **All three consumers adopt COW in this phase** (user decision). Order:
   crate branch (7a) → commit (7b, fixes #420/#423) → rebase (7c, fixes
   #421) → bench (7d, lifts the last gate; a feature, not a fix). Each op
   adoption is its own commit and lands only when its full existing suite
   plus the new COW parity tests pass.

2. **#432 is fixed fix-first as a standalone commit before the COW work**
   (user decision). The read defect is independent of COW (blast radius
   rebase/convert/compare/bench) and the COW read path must build on a
   correct chain reader. 7z adds a `QCOW_OFLAG_ZERO` (bit 0) constant and
   a `ClusterLookup::Zero` verdict, fixes the classic-L2 arm of
   `Qcow2State::cluster_lookup` (`crates/qcow2/src/lib.rs:2223-2238`) and
   the twin map classifier `classify_qcow2_l2_standard` (`:1692`,
   self-documented gap at `:1684-1691`), teaches `read_chain_virtual_*`
   to emit zeros for a zero verdict (both host==0 and host!=0 cases),
   and lands regression tests exercising the fix through rebase and bench
   chain reads (commit reads overlay host-offsets directly so it is not on
   the #432 path — note this). quirks.md `:1456-1472` updated from
   "deferred" to "fixed". The **write-side** zero-flag policy is a separate,
   smaller decision folded into 7a (see decision 6).

3. **Adopt the shared refcount-growth planner in commit and rebase** (user
   decision). COW allocates data/L2 clusters and the L2-COW child-increment
   can expand coverage; without growth, commit/rebase would refuse
   `RefcountExhausted` at a refblock boundary where qemu grows and
   succeeds. The **planner** (`qcow2_write::growth::plan_refcount_growth`)
   is already shared (phase 6); the **execution** (`qcow2_grow_refcounts`)
   currently lives only in bench's `main.rs`. 7a resolves how commit/rebase
   reuse it — see decision 5.

4. **Lift the three refusal layers, keep the deep classification as the
   COW trigger.** (a) Remove the op-level early `nb_snapshots` gates
   (commit `main.rs:879/882` → `ERROR_BACKING/OVERLAY_HAS_SNAPSHOTS` 14/15;
   rebase `:846` → `ERROR_OVERLAY_HAS_SNAPSHOTS` 14; bench `:642`). (b)
   Relax the envelope `Gate::HasSnapshots` (`crate lib.rs:630`, wire 7):
   make it a **policy** — `check_envelope` gains an `allow_snapshots` /
   `cow_enabled` capability (default preserves the refuse for any future
   non-COW caller; all three consumers pass it true) rather than being
   deleted, so the crate stays safe-by-default. (c) Turn the per-cluster
   `SnapshotShared` (`:1416/:1423`) and `SnapshotSharedL2Table`
   (`:1445/:1452`) classifications from `Err` into COW emission. All three
   layers must move together or the envelope refuses before the classifier
   ever runs. The `-u` rebase path (`run_qcow2_unsafe`, no gate) and
   `test_unsafe_rebase_overlay_internal_snapshots_matches_qemu` stay
   untouched and green.

5. **Growth execution is shared, not re-implemented per op — 7a picks the
   mechanism and 7p de-risks it.** Two candidates, to be settled in 7a
   against the 7p findings: **(A) extract** bench's `qcow2_grow_refcounts`
   (+ `flush_dirty_refblocks`, the staged-set helpers) into a shared
   guest-op growth-execution helper (a small `no_std` module or a
   `crates/qcow2-write-exec` extension) that commit, rebase and bench all
   call — the least-invasive reuse, keeping the phase-6 "execution stays
   op-side" posture but de-duplicated; or **(B) step-shape** growth (the
   phase-6 decision-1 recorded future work) so the planner emits growth as
   steps the executor runs, and every consumer inherits it for free — the
   cleaner end state but a larger crate change. Default recommendation: (A)
   for phase 7 (smaller, proven code moved verbatim, the #433
   materialization fix carried along), with (B) noted as the phase-8/9
   cleanup. The `worst_case_touched` estimate is bench-schedule-specific;
   commit/rebase compute their own worst-case new-cluster count (COW
   allocates at most one new data cluster + one new L2 per touched shared
   cluster) and pass it across the boundary, exactly as bench passes
   `worst_case_new_clusters`. **7p refinement:** the worst-case sizing must
   count **refblock** growth even when the refcount *table* itself does not
   grow (probe 4 saw a new refblock added with `refcount_table_clusters`
   unchanged) — size on new refblocks needed, not on table-cluster growth.

6. **Zero-flag WRITE-target policy (folded into 7a, small; 7p-REFINED).**
   Independent of the #432 read fix, `classify_l2_entry` still refuses a
   zero-flag *target* entry as `UnknownL2Entry` (`lib.rs:1406-1409`). 7p
   settled the exact qemu semantic (qemu does NOT free the old offset):
   - **host offset == 0** (zero flag, no allocation) → classify
     `Unallocated`: allocate a fresh cluster / zero-fill the uncovered
     range.
   - **host offset != 0** (zero flag over an allocated cluster) → classify
     it EXACTLY like a `Standard` cluster by its refcount: **rc 1 → Owned**
     (overwrite in place, clearing the zero bit; qemu reuses the offset,
     no free), **rc > 1 → `SnapshotShared`** (COW). There is NO
     "free the old offset" step — the original plan's blanket "treat as
     Unallocated → allocate fresh" would leak the old host cluster
     (rc 1, unreferenced → check dirty) or skip a required COW.
   A classification refinement with its own unit tests (both host==0 and
   host!=0 × rc). If 7a chooses to defer the host!=0 arm, **keep the
   `UnknownL2Entry` refusal for it** — do not allocate-and-leak.

7. **Wire codes.** COW *removes* refusal codes rather than adding them: the
   op-level `*_HAS_SNAPSHOTS` gates and the crate's `SnapshotShared` /
   `SnapshotSharedL2Table` renderings become unreachable for the COW-
   enabled path (keep the constants + host messages for the
   non-COW-capability default and for defensive mapping, but they no longer
   fire on snapshot-bearing images). `RefcountExhausted` (commit 11, rebase
   10) stays as the ceiling *after* growth is exhausted (growth has its own
   envelope). Any genuinely new internal-inconsistency COW refusal (e.g. a
   decrement underflow, a shared cluster with an impossible refcount) maps
   to each op's existing `*_INCONSISTENT` family, not a new code.

8. **Crate changes are IN SCOPE this phase** (unlike 4-6). Phase 7 is where
   `crates/qcow2-write` grows COW. The bar is the crate's own review
   standard (no_std, pure, unit-tested, simulation-covered) — not the
   phases-4-6 "no crate change" freeze. `crates/qcow2` also changes (the
   #432 read fix). No other crate churn.

### COW semantics & refcount contract (the parity contract)

This replaces the phases-4-6 divergence budget. Each row is a property the
implementation must satisfy and a test pins.

| # | Property | Verified by |
|---|----------|-------------|
| C1 | Data-cluster COW: shared `D` copied to fresh `D'`, L2 → `D'\|COPIED`, `rc(D')=1`, `rc(D)`−1; `D` never freed (snapshot holds it) | crate unit test + snapshot read-back |
| C2 | L2-table COW: shared `T` copied to `T'`, L1 → `T'\|COPIED`, `rc(T')=1`, `rc(T)`−1; child data-cluster refcounts **UNCHANGED** (already rc≥2 from snapshot creation — 7p; NO child-increment, which would corrupt to rc3) | crate unit test asserting children stay rc≥2 + `qemu-img check` clean (zero rc3) |
| C3 | Nested COW: a write through a freshly-COWed `T'` whose child `D` is now shared (C2) triggers C1 on `D` in the same pass | crate unit + integration |
| C4 | Crash order: copied data durable before the L2/L1 pointer flip; all refcount changes (incl. decrements + child increments) last, behind the final barrier | ordering test over the emitted step program |
| C5 | Active view after COW is `qemu-img compare`-identical to qemu's result; `qemu-img check` clean (no `refcount=1 reference=2`, no leaks) | integration parity |
| C6 | commit: every pre-existing snapshot's virtual view bit-identical to its **pre-commit** content (qemu preserves) | snapshot read-back oracle |
| C7 | rebase safe: every pre-existing snapshot's virtual view bit-identical to **the snapshot read through the NEW backing** (qemu's read-through semantic), matching qemu's result | snapshot read-back oracle |
| C8 | bench: snapshots preserved (like C6); active view compare+check vs a qemu twin | integration parity |
| C9 | Growth during COW: a COW schedule that crosses a refblock boundary grows (does not `RefcountExhausted`) and stays check-clean, carrying the #433 materialization invariant | integration + growth reuse tests |
| C10 | #432: a classic zero-flag backing cluster reads as zeros (host==0 and host!=0) through the chain reader; rebase/bench COW fills are correct | 7z regression tests |
| C11 | Placement freedom: instar's COW output need NOT byte-match qemu; only C5-C8 constrain it. No byte-identity assertion anywhere in the COW proof | (design constraint — proof uses compare/check/read-back only) |
| C12 | Non-snapshot images and the `-u` rebase path are byte-for-byte unchanged from phase 6 (COW is dormant when nothing is shared) | full existing suites unchanged + `-u` parity test green |

### The snapshot read-back oracle (new; minimal in phase 7)

A reusable test helper `tests/helpers/`: given an image and a snapshot
name, extract that snapshot's virtual view as a raw sha256 —
`qemu-img snapshot -a <name>` **on a copy** (never the original), then
`qemu-img convert -O raw` (or a full read), then sha256. Compare
instar's post-op image's snapshot read-back against **qemu's post-op
image's** snapshot read-back on the same fixture (compute both live with
the pinned qemu-img). Expected value is defined per op (C6/C7/C8). Phase 7
drives it over the phase-1 Q1/Q2 fixture recipes (documented in the master
plan findings) across the pinned qemu versions; phase 8 generalises the
generator into differential-fuzz.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7p | high | default | none | Empirical probes on the pre-COW binary + live qemu (scratchpad only, no tree changes). (1) Re-confirm qemu's COW refcount bookkeeping on the Q1 fixture: after `qemu-img commit` into a snapshot-shared backing, dump the exact refcounts of the COWed data clusters, the old vs new L2 tables, and every child of a COWed L2 — this pins C1/C2's numbers (esp. the child 1→2 increment) across the pinned versions. (2) The snapshot read-back oracle prototype: build the helper and confirm it discriminates Q1 mode-A corruption (preserved vs bled) and the Q2 read-through-new-backing semantic. (3) #432 live: build both a host==0 and a host!=0 classic zero-flag backing cluster (`qemu-io write -z`), read through the chain with today's binary, capture the mis-fill — the 7z regression fixtures. (4) COW+growth interaction: find the smallest snapshot-bearing fixture whose COW schedule crosses a refblock boundary (to exercise C9) and one that does not. (5) Nested-COW: a fixture with a snapshot-shared active L2 AND shared data under it, to pin C3 ordering. (6) Zero-flag WRITE target (decision 6): does qemu free the old host offset when overwriting a zero-flag-with-offset entry? — settles whether "treat as unallocated" is complete. Report recipes + the refcount tables + the read-back expected values per op. |
| 7z | medium | default | none | The #432 read-path fix, standalone fix-first commit (decision 2). Add `QCOW_OFLAG_ZERO` (bit 0) to `crates/qcow2`; add a `ClusterLookup::Zero` verdict; fix the classic-L2 arm of `Qcow2State::cluster_lookup` (`lib.rs:2223-2238`) to return `Zero` when bit 0 is set (both host==0 and host!=0), and the twin `classify_qcow2_l2_standard` (`:1692`); teach `read_chain_virtual_cluster`/`_range` (`:5531/:6243`) to zero-fill the output for a `Zero` verdict instead of recursing to backing / reading stale host bytes. Regression tests using the 7p fixtures, exercised through `read_chain_virtual_*` directly AND end-to-end through rebase (`read_chain_cluster` `:1575`) and bench (`main.rs:1289`). Update `docs/quirks.md:1456-1472` from deferred→fixed. Do NOT touch the write planner or COW. make instar/lint/test-rust/sizes/`stestr run test_rebase test_bench`/pre-commit. STOP if the fix needs a write-side change (it should not). |
| 7a | high | default | none | The crate COW branch — the phase's core (read this plan §"core net-new work" + the C-contract; grounded in the classification at `lib.rs:1395-1455`). Implement in `crates/qcow2-write`: (i) the `allow_snapshots`/`cow_enabled` capability on `check_envelope` relaxing `Gate::HasSnapshots` (decision 4b); (ii) data-cluster COW emission for `SnapshotShared` (C1: ReadCluster old→Bounce, alloc `D'`, WriteRange→`D'`, PatchEntryU64 L2, refcount +1 new / −1 old; sub-cluster RMW as today); (iii) L2-table COW emission for `SnapshotSharedL2Table` (C2, 7p-CORRECTED: alloc `T'`, copy, PatchEntryU64 L1, `rc(T')=1`, `rc(T)`−1, and **NO child-refcount change** — children are already rc≥2 from snapshot creation; a child-increment corrupts to rc3; then proceed into `T'`, whose children still classify `SnapshotShared` so per-child C1 fires per write); (iv) nested COW (C3); (v) the **refcount-decrement primitive** in the staged refblocks (net-new — the snapshot allocator only increments); (vi) decision 6 zero-flag-target classification (host==0 → Unallocated; host!=0 → Standard-by-refcount: rc1 Owned-overwrite-clearing-zero-bit, rc>1 SnapshotShared; NO free-old-offset); (vii) ordering per C4. PLUS the growth-execution sharing (decision 5): extract bench's `qcow2_grow_refcounts` + helpers into a shared guest-op growth module callable by all three ops (mechanism A unless 7p favours B — if B, that is a bigger crate change, flag before committing). Unit tests for C1-C4/C6-semantics + extend the phase-3 simulation harness with COW schedules (shared-data, shared-L2, nested, growth-crossing). make instar/lint/test-rust (quote the crate's line)/sizes/pre-commit. This step changes crates — that is expected (decision 8); STOP only if it needs a NEW StepKind (it should not) or a crate other than qcow2-write/qcow2. |
| 7b | high | default | none | Migrate commit onto COW + growth. Remove the op gates (`main.rs:879/882`); pass `cow_enabled` to `check_envelope`; route `SnapshotShared`/`SnapshotSharedL2Table` into the crate's COW emission (no longer `ERROR_BACKING_INCONSISTENT`); wire the shared growth execution (decision 5) with commit's own worst-case-new-cluster count; keep `RefcountExhausted` (11) as the post-growth ceiling. Rewrite `TestCommitSnapshotGate` (`test_commit.py:911/:963`): refusal → COW success, sha-unchanged → snapshot-read-back-preserved (C6) + active-view compare + check-clean (C5) vs a qemu twin, over the Q1 fixture matrix. Existing non-snapshot commit suite + baselines UNCHANGED (C12). make instar/lint/test-rust/sizes/`stestr run test_commit`/pre-commit. STOP on any C5/C6 parity failure with decoded evidence (refcount dump + read-back diff). |
| 7c | high | default | none | Migrate rebase safe mode onto COW + growth. Remove the gate (`main.rs:846`); the **read-through-new-backing** snapshot semantic (C7) is the load-bearing subtlety — the snapshot's virtual view must resolve through the new backing after rebase, matching qemu (Q2), NOT stay at old content and NOT be corrupted. COW the shared active L2 (C2) so the `refcount=1 reference=2` × 80 data-loss chain (#421) cannot occur. Wire growth (decision 5). Keep `run_qcow2_unsafe` + `test_unsafe_rebase_overlay_internal_snapshots_matches_qemu` (`:895`) untouched and green. Rewrite `TestRebaseSnapshotGate` (`test_rebase.py:837`): refusal → COW success with C7 read-back + C5 compare/check vs a qemu twin, over the Q2 fixture matrix (cs {512,65536} × psw {yes,no}). Note the cs=512 path (#422 fixed in phase 2; confirm no regression). make instar/lint/test-rust/sizes/`stestr run test_rebase`/pre-commit. STOP on C5/C7 parity failure with decoded evidence. |
| 7d | medium | default | none | Adopt COW in bench `-w` (lift the last gate, `main.rs:642`). Remove the `nb_snapshots` gate; pass `cow_enabled`; route `SnapshotShared`/`SnapshotSharedL2Table` into COW (bench already imports growth, so C9 is inherited); snapshots preserved (C8). New integration test: `bench -w` into a snapshot-bearing image → check-clean + active-view compare vs a qemu twin + snapshot read-back preserved. Existing bench suite (incl. the 76 from phase 6 and the old refusal test, now rewritten) UNCHANGED otherwise. make instar/lint/test-rust/sizes/`stestr run test_bench`/pre-commit. STOP on C8 failure. |
| 7e | high | default | none | The COW parity harness + soak. Land the snapshot read-back oracle as a reusable `tests/helpers/` module (apply-on-copy → convert → sha256, per-op expected value C6/C7/C8) and a parity driver that runs the Q1/Q2 (+ bench) fixture matrices across the pinned qemu versions, computing qemu's result live and asserting C5-C8 + check-clean. Run it. Then a COW-focused differential-fuzz smoke (a small snapshot-bearing seed set — the FULL fuzz generator is phase 8): assert 0 divergences on the read-back + compare + check oracle. STOP on any parity/divergence failure with decoded evidence. (Harness/tests only; no src changes.) |
| 7f | medium | default | none | Docs close-out. quirks.md (COW behaviour, the per-op snapshot-view semantic C6/C7, #432 fixed, the zero-flag-target policy, the growth-for-commit/rebase note); docs/{commit,rebase,bench}.md; ARCHITECTURE.md (COW capability, refcount COW machinery, shared growth execution); CHANGELOG (COW + #432 + growth-for-mutators); master plan execution table row 7 → Complete + a "Findings: phase 7 COW" section (the C-contract results, qemu refcount parity numbers, the read-back oracle technique, what phase 8 scales) + defect register: **#420/#421/#423 RESOLVED by COW** (interim gates lifted), **#432 fixed**, #422 noted; plans index; README/AGENTS if user-facing. pre-commit. |

Ordering: 7p first; 7z (fix-first) after 7p, independent of the COW
steps; 7a after 7z; 7b/7c/7d sequential after 7a (each its own commit;
7d may follow 7c or run once 7a is proven); 7e after the consumers;
7f last. One commit per step minimum; management reviews and commits.

## Review checklist deltas

Phases-4-6 deltas apply where they still make sense, plus:

* **No before/after byte-identity anywhere on the COW surface** (C11).
  The proof is qemu-parity (compare + check + snapshot read-back). A
  reviewer must confirm no step smuggles in a byte-equality assertion
  against a frozen baseline for a snapshot-bearing image.
* **The snapshot read-back oracle's expected value is per-op** (C6 vs
  C7) — a reviewer confirms commit asserts *preserved* and rebase asserts
  *read-through-new-backing*, not the reverse.
* **`qemu-img check` clean is mandatory on every COW result** — the
  `refcount=1 reference=2` class (#420/#421 mode B) and leaks are exactly
  what COW must eliminate; a check-dirty COW output is a STOP.
* **L2-COW must NOT touch child refcounts (C2, 7p)** — the highest-risk
  arithmetic is a trap: children are already rc≥2 from snapshot creation,
  so a child-increment corrupts them to rc3 (check-dirty). Reviewer
  confirms L2-COW only copies + repoints + `rc(T')=1` + `rc(T)`−1, and a
  test asserts zero rc3 clusters after a shared-L2 write.
* **Decrement never frees a live cluster** — a COWed old cluster is still
  snapshot-referenced (rc ≥ 1 after −1); a reviewer confirms no path
  reaches rc 0 on a cluster a snapshot still maps (the #421 failure mode
  inverted).
* **The `-u` rebase path and all non-snapshot outputs are byte-unchanged**
  (C12) — COW must be dormant when nothing is shared; the existing suites
  and baselines pass unchanged.
* **#432 fix (7z) is verified through a real chain read**, not just the
  unit — both host==0 and host!=0 zero-flag cases, via rebase and bench.
* **Crate changes are expected (decision 8)** but bounded to
  qcow2-write (COW) and qcow2 (#432); any other crate churn, or a new
  `StepKind`, is a STOP-and-discuss.

## Findings: 7p probes (2026-07-13)

All six 7p probes ran against the pre-COW binary + pinned qemu-img
(6.2.0 / 8.2.0 / 10.2.0, version-invariant). Full report and reusable
fixtures/scripts in scratchpad (`7p/FINDINGS.txt`, `7p/qcow2dump.py`,
`7p/oracle/`, `7p/z432/`, `7p/fixtures/`); 7f folds the durable facts
into docs. Two findings materially corrected this plan **before** 7a
(applied above); the rest confirm the C-contract.

1. **C2 child-increment was WRONG and would corrupt — corrected.** qemu
   eagerly bumps every reachable data cluster to rc≥2 (COPIED clear) at
   *snapshot-creation* time, so at L2-COW the children are already rc≥2;
   copying `T→T'` redistributes the reference (net child delta 0) and
   qemu leaves children untouched (histogram `{1:40, 2:16}`, zero rc3,
   check clean, all versions). L2-COW must NOT increment children;
   they already classify `SnapshotShared` so per-child C1 fires per
   write. Plan §core-net-new-work, C2, decision-in-7a, and the review
   checklist are corrected accordingly.
2. **Decision-6 zero-flag target refined — qemu does NOT free the old
   offset.** host==0 → allocate fresh (Unallocated); host!=0 rc1 →
   reuse in place, clear the zero bit (Owned overwrite); host!=0 rc>1 →
   COW (SnapshotShared). The original blanket "treat as Unallocated"
   would leak the host!=0 cluster. Decision 6 corrected.
3. **C1 confirmed** (data-cluster COW: `D`(rc2)→`D'`(rc1,COPIED),
   old `D` 2→1, never freed).
4. **C3 nested COW confirmed** — a single write into a shared-L2-over-
   shared-data fixture COWs `T→T'` (children untouched, still rc2) then
   COWs the target `D→D'` through `T'`; re-confirms finding 1.
5. **C6/C7 read-back oracle prototyped** and the per-op expected shas
   recorded (commit = PRESERVED: snap1 pre==post; rebase =
   READ-THROUGH-NEW-BACKING: snap1 after == apply-snap1→`rebase -u`
   onto base_new→convert). The oracle catches Q1 mode-A corruption that
   `qemu-img check` passes clean. → 7e's expected values.
6. **#432 fixtures built** — Case A (host==0, reads through to a lower
   backing) and Case B (host!=0, reads stale host bytes), both
   reproduced on today's binary via `instar convert` and `rebase -b ''`.
   → 7z regression tests.
7. **Growth (C9):** a cross-refblock-boundary snapshot fixture and a
   within-headroom one recorded; plus the sizing refinement in
   decision 5 (count refblock growth even when the RT does not grow).
