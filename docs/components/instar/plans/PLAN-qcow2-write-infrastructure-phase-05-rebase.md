# qcow2 write infrastructure — phase 05: migrate rebase

Parent:
[PLAN-qcow2-write-infrastructure.md](/components/instar/plans/PLAN-qcow2-write-infrastructure/).
Planned at medium effort per the master plan (the pattern is
proven; phase 4 is the template —
[PLAN-qcow2-write-infrastructure-phase-04-commit.md](/components/instar/plans/PLAN-qcow2-write-infrastructure-phase-04-commit/)).
Migrates **safe-mode** `instar rebase` (including safe detach)
onto `crates/qcow2-write` + `crates/qcow2-write-exec`. The `-u`
metadata-only path and unsafe detach are untouched (they write
only the header-field/path bytes via the existing ≤2-patch plan);
the vmdk path is untouched. Bar: pure refactor from the outside —
full existing test surface unchanged, plus the migration-proof
oracle (instar-before vs instar-after byte identity; determinism
premise first).

Grounded in a fresh survey (2026-07-13). Load-bearing verified
facts: rebase's hand-rolled `allocate_overlay_cluster_qcow2`
(src/crates/rebase/src/qcow2.rs:455-519) is mechanically
identical to qcow2-write's `alloc_cluster` path for count==1
(same first-fit flat scan from a persistent (0,0) cursor, same
claim-time staged refcount=1 write, same one-past-claim advance,
same `flat_index * cluster_size` result, same dirty marking), and
the loop allocates fresh-L2 before data within an iteration
(main.rs:877 vs :907) — commit's order, which the crate
reproduces. The walk is strictly ascending in virtual-cluster
order with one visit per cluster (main.rs:785-794), so first
touch is ascending `l1_idx` and an evicted L2 table is never
re-touched. Byte identity is therefore achievable, with the
same caveats class as phase 4 plus three rebase-specific ones
settled below.

## Scope

Three deliverables: the op migration (5a), the proof
(`scripts/migration-proof.py --op rebase` — the registry slot at
scripts/migration-proof.py:413 is ready) plus fuzz/suite runs
(5b), and docs (5c); preceded by empirical probes (5p). No new
crates. One sanctioned-change escape hatch as in phase 4: any
qcow2-write / qcow2-write-exec source change stops the phase and
comes back to the management session.

### Settled design decisions (binding)

1. **The skip probe stays op-side and probes ORIGINAL pre-run
   state.** Unlike commit, rebase must decide per cluster
   whether to involve the planner at all: a cluster already
   mapped in the overlay is skipped (today: any non-zero L2
   entry — compressed, zero-flag and garbage entries all skip,
   main.rs:806-813; calling plan_write on a mapped cluster
   would classify Owned and wrongly overwrite it with old-chain
   content). Post-migration the probe consults the ORIGINAL
   overlay mapping: a read-only staged copy of the pre-run L1
   (separate from the planner's `StagedRegions.l1`, which the
   executor patches), plus a single cluster-sized probe buffer
   holding the current original L2 table, reloaded on `l1_idx`
   change via qcow2-write-exec's `read_bytes` (the walk is
   sequential, so exactly one read per populated table).
   Clusters under an originally-empty L1 slot are unmapped by
   definition (fresh tables never probe disk — they exist only
   in the planner's window until flushed). This is semantically
   identical to today's live-staged probe: patches only ever
   cover already-visited clusters, and fresh tables were
   L1-empty originally. The probe's skip predicate is
   byte-for-byte today's (`entry != 0` after masking — R-D2's
   overlay-side skip semantics preserved exactly; the crate's
   compressed/unknown-entry refusals stay unreachable for
   mapped clusters).
2. **Stage-everything for existing L2s is retired** (the probe
   buffer replaces it). This lifts today's staging-time
   capacity refusals for many-L2 overlays (wire 9 even when
   nothing needs copying) — a deliberate R-D6 widening, probed
   off-matrix (P8). The growable L2 arena and its caps vanish
   with it (the #422 hazard class is gone by construction).
3. **Carve and window sizing.** Reclaim the 4 MiB
   `PLANNER_SCRATCH` duplicate (only a ~1.1 KiB residual
   planner scratch remains for the -u/detach/vmdk path
   planners). New backing... overlay-side planner carve mirrors
   commit's phase-4 template: staged L1 64K + original-L1 copy
   64K + staged RT 64K + dense-prefix refblocks 3 MiB
   (`min(2048, 3 MiB/cs)`) + L2 window 2 MiB
   (`min(256, 2 MiB/cs)` slots) + probe L2 buffer 1 MiB (one
   cluster at the 1 MiB cap) + step buffer 64K + planner bounce
   1 MiB + RMW/fill sectors + the retained CHAIN_CACHES 2 MiB
   and COMPARE_BUFS 2 MiB (`old_buf` doubles as
   `RegionId::CallerData`). Fact-base estimate ≈ 10.7 MiB of
   the 12.44 MiB window. 5a must compute today's joint
   refblock/L2 envelope (both shared the 4 MiB bump arena) vs
   the fixed regions and verify no narrowing is reachable on
   the test/fuzz envelope (images ≤ 64 MiB, cs ≤ 1 MiB); any
   theoretical narrowing beyond it is documented and probed
   (R-D6 both directions). Cluster cap stays 1 MiB
   (COMPARE_BUF_SIZE, unchanged gate).
4. **Eviction is reachable and accepted.** With a 2 MiB window,
   overlays at cs ≥ 64K can touch more tables than slots. The
   structural safety argument (binding, and 5b verifies its
   consequence): the walk never revisits a table, so an evicted
   table is dirty-written exactly once and never reloaded —
   final bytes identical to today's end-of-loop flush; only
   intermediate I/O order differs (R-D5, accepted; no crash
   contract exists — zero fsyncs today, preserved by D8
   degradation).
5. **Driving loop**: per non-skipped cluster, chain reads into
   `old_buf`/`new_buf` exactly as today (full physical cluster,
   including any beyond-EOV tail — the compare stays
   full-cluster for byte-identical copy decisions, R-D9), skip
   if equal; else
   `plan_write(voff, min(cs, virtual_size - voff),
   CallerData{0})` under the decision-1 window protocol,
   executing via qcow2-write-exec with
   `StagingConfig.device = Output`. On a non-BufFull planner
   error, execute the already-emitted window first (commit
   4b's rule — reproduces the #422-fixture wire-10 partial
   state byte-for-byte). After the loop: plan_flush epochs,
   THEN the untouched deferred header/backing-path patch group
   (`apply_rebase_plan`) — preserving the Q4 "header lands
   after refcounts" order. Chain reads, `buffers_eq`, counters
   (`clusters_copied`/`bytes_copied`, observable in
   `--output json`) and the `l1_idx >= l1_size` defensive break
   are unchanged.
6. **Envelope + wire codes (the phase-4 template applied).**
   `check_envelope` on the overlay header after the existing
   gates. Two appended `RebaseResult` codes:
   `ERROR_OVERLAY_UNSUPPORTED = 15` (Gate::UnknownIncompatible
   + Gate::ExtendedL2 — rebase today has NO gate for zstd,
   unknown incompatible bits or extended-L2 anywhere, and safe
   mode would misread 16-byte extended-L2 entries as 8-byte:
   probable live corruption, probed by 5p) and
   `ERROR_OVERLAY_INCONSISTENT = 16` (classification refusals,
   staging-time RT contiguity/reserved-bit refusals — all
   pre-mutation). `RefcountExhausted` keeps wire 10 and its
   pinned message; envelope gates duplicating existing op/
   planner gates keep existing codes (unreachable — existing
   gates fire first); `CompressedCluster` is unreachable behind
   the skip probe, defensively mapped to wire 1;
   planner-protocol errors map to 13 (internal family). Host
   messages in the house style; completeness tests extended.
7. **R-D4 is a live defect and this migration is its fix.**
   Rebase stages refblocks by compacting non-zero RT entries
   with no contiguity gate (main.rs:551-613) and the allocator
   indexes them as dense — the holed-RT misallocation (#428's
   sibling) on the overlay, which is exactly the file the 4p
   shrink recipe operates on. 5p confirms the corruption
   empirically and drafts the issue (user files); 5a's
   dense-prefix + contiguity gate refuses it pre-mutation
   (code 16).
8. **EOV tail (R-D9).** The plan_write request is clamped to
   `virtual_size - voff`; the 4q rule classifies it as full
   coverage. Divergence vs today: beyond-EOV bytes of a copied
   tail cluster come from the old chain today (backings may
   outsize the overlay and carry non-zero bytes there) vs the
   crate's zero-fill. Virtual content identical; raw-file
   divergence lands in the proof harness's D9 fallback bucket
   (virtual-content equality) and is accepted with this
   rationale if 5p shows the shape is real. The full-cluster
   compare (decision 5) keeps copy DECISIONS identical either
   way.
9. **crates/rebase slims** (decision-8 analog): delete
   `allocate_overlay_cluster_qcow2`, `AllocationState`, the
   PLANNER_SCRATCH refblock copy + dirty bitmap from
   `RebaseQcow2SafeContext` (context reduces to geometry + the
   deferred-metadata plan), and the 4 superseded allocator unit
   tests. The -u/detach/vmdk planner paths and their tests are
   untouched; the deferred-metadata plan machinery survives
   as-is.
10. **Out of scope**, recorded: the compressed-chain-member
    refusal (wire 12 mid-loop today where qemu succeeds — a
    pre-existing divergence, R-D2's chain half; lifting it
    means enabling decompress in the rebase binary, a size and
    scope question for later), the v3 zero-flag chain-reader
    bugs (P7 — pre-existing `crates/qcow2` chain-read
    behaviour, probed and issue-drafted if confirmed but NOT
    fixed here), and the vacuous `NewBackingIncompatible` gate
    (P9 — documented, not changed).

### Divergence budget (rebase edition)

| # | Divergence | Verdict |
|---|-----------|---------|
| R-D1 | `check_envelope` refuses extended-L2 / zstd / unknown-incompat overlays; today no gate exists and ext-L2 safe rebase probably corrupts | Accept — spec-mandated + live-defect fix; code 15; probe P4/P5; refusal tests |
| R-D2 | Compressed overlay entries: skipped today, skipped after (decision 1) — NO behaviour change. Compressed chain members: wire-12 refusal today, unchanged (decision 10) | Accept — probe P2/P3 pins both |
| R-D3 | Classification refusals on inconsistent-but-gate-passing overlays (COPIED/refcount/reserved bits) | Accept — unreachable on check-clean images; code 16 |
| R-D4 | Holed-RT overlay: live misallocation today → contiguity-gate refusal (code 16, pre-mutation) | Accept — live-defect fix; probe P1; issue draft for user; refusal test from recipe |
| R-D5 | Flush order first-touch + mid-loop eviction writebacks (reachable at cs ≥ 64K) vs staged-array end-of-loop flush | Accept — final bytes identical (walk never revisits); proof is the arbiter |
| R-D6 | Capacity envelope: stage-everything refusals lift (widening); fixed regions vs today's joint 4 MiB budget could narrow exotic shapes | Accept — 5a verifies no narrowing on the ≤64 MiB test envelope; off-matrix probes both directions (P8 + a refblock-heavy shape) |
| R-D7 | Wire codes 15/16 appended | Decision 6 |
| R-D8 | Durability barriers degrade on Output; zero fsyncs preserved | Accept — no-op |
| R-D9 | EOV tail: old-chain bytes today vs crate zero-fill beyond EOV | Decision 8; probe P6 |
| R-D10 | Non-BufFull error executes emitted window first | Commit 4b's rule, applied |

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5p | medium | default (Fable) | none | Empirical probes on the pre-migration binary (management saves a runnable dist first — phase-4 `probes-4p/dist` precedent). No tree changes; scratchpad only; findings as text. P1: holed-RT OVERLAY (4p shrink recipe on the overlay) + safe rebase with ≥1 divergent cluster — capture the misallocation (check errors/leaks) vs qemu twin; draft the issue (sibling of #428, do NOT file). P4/P5: extended-L2 overlay and zstd-bit overlay safe rebase — today's behaviour (expect corruption / misparse) vs qemu; draft issue if live corruption confirmed. P2/P3: compressed overlay entries (expect clean skip + qemu parity) and a compressed chain member (expect wire-12 refusal post-partial-writes; document qemu's success). P6: unaligned virtual size incl. an old backing LARGER than the overlay with non-zero bytes beyond the overlay's EOV — does today's copy carry old-chain tail bytes into the overlay (the R-D9 raw divergence shape)? Chained variant too. P7: `qemu-io write -z` zero-flag clusters in a chain member — today's read (expect fall-through/stale bytes) vs qemu's zeros; draft issue if confirmed (pre-existing chain-reader defect, NOT fixed by this phase). P8: many-populated-L2 overlay with zero divergent clusters (today wire 9 at staging; the widening exemplar). P9: new backing smaller than overlay (the vacuous gate; behaviour vs qemu). Also: refusal inventory over the intended 5b matrix (which combos refuse today and why) and run-to-run determinism spot-check. Report with exact recipes. |
| 5a | high | default (Fable) | none | The migration per settled decisions 1-9 (read this plan, the phase-4 plan, the master plan's phase-4 findings, and the fact anchors). Sequential after 5p (its findings gate the test list). Re-carve per decision 3 with the envelope computation; original-state skip probe per decision 1; driving loop per decision 5; wire codes 15/16 per decision 6 (+ host messages + completeness tests); dense-prefix contiguity-gated staging per decision 7; slim crates/rebase per decision 9; re-target the rebase fuzz target if it consumes deleted API (the phase-4 precedent). New integration tests: R-D4 holed-RT refusal (P1 recipe, sha256-unchanged), R-D1 ext-L2 + zstd refusals (code 15, sha256-unchanged), P8 widening success (check-clean + info-equivalence vs qemu twin), and an eviction-exercising safe rebase (cs=64K, >32 touched tables) proving content parity vs qemu. Byte-identity spot checks vs the saved pre-migration dist: one 64K seeded safe rebase, one 512-byte shape, one detach-safe, AND the #422 fixture (wire-10 refusal, scaffolding identity). Any mismatch: STOP and decode. Full bar: make instar / lint / sizes / test-rust / `stestr run test_rebase` / pre-commit. Do not touch qcow2-write, qcow2-write-exec, the -u/detach-unsafe planner paths, the vmdk path, or the deferred-metadata machinery. No commit. |
| 5b | high | default (Fable) | none | Extend scripts/migration-proof.py with `--op rebase` (registry slot ready) and run the proof. Matrix (deterministic, seeded so safe mode actually copies — the fuzz picker's bare images copy nothing): cluster_size {512, 4096, 65536} × overlay size {1M, 64M} × chain shape {2-chain rebase-to-base, 3-chain rebase-to-mid, safe detach} × seed {divergent old/new content, identical old/new content, empty} × mode {safe, -u} minus incoherent combos, plus an unaligned-size combo and an oversized-old-backing combo (P6's shape). Bucketing rules as phase 4 (both-refuse demands scaffolding identity; refuse-before/succeed-after must match 5p's inventory; D9 fallback = virtual-content equality, expected only on the oversized-backing shape if 5p confirmed it). Off-matrix: P8 widening exemplar + the refblock-heavy narrowing check from 5a's envelope computation. Then `scripts/differential-fuzz.py --iterations 300 --ops rebase` (0 divergences expected) and the full rebase integration suite + baseline matrix. STOP on any identity/determinism failure with offsets decoded. New-file/extend-only: scripts/migration-proof.py. No commit. |
| 5c | medium | default (Fable) | none | Close-out: quirks.md rebase section (codes 15/16, the R-D4 fix, capacity changes, EOV-tail note, the out-of-scope compressed-chain and zero-flag chain-reader records), docs/rebase.md refusal table, CHANGELOG, master plan execution table phase 5 → Complete + "Findings: phase 5 rebase migration" (proof numbers, defect outcomes + issue status, the skip-probe design as the phase-6 pattern hint, eviction-reachable-but-safe argument, envelope outcomes), plans index, defect register entries for the 5p-confirmed defects (issue numbers only if the user has filed by then — otherwise the phase-4 "pending" phrasing). pre-commit. |

5p first (management saves the pre-migration dist before it);
5a after 5p; 5b's run after 5a lands (harness authoring may
overlap 5a); 5c last. One commit per step minimum; management
reviews and commits.

5p outcome notes (2026-07-13, binding on 5a/5b):

* R-D4 and R-D1 both CONFIRMED live (holed-RT overlay:
  1092 check errors + 32 leaks at exit 0, or wire-10 refusal
  after 12.4 MB of mis-placed scribbles on larger divergences;
  extended-L2: silent virtual-content corruption at exit 0).
  Issue drafts in the session scratchpad await the user's
  filing decision. The #428 recipe self-masks on qemu ≥ 10
  (discard now produces zero-plain clusters); 5a's refusal
  test must use 5p's adapted recipe (pre-touch + partial
  write + discard 8M-32M + shrink to 82M, backing attached
  via `rebase -u` afterwards).
* R-D9 refined: qemu-img rebase ALSO carries beyond-EOV
  old-chain bytes byte-identically to instar-before, so the
  crate's zero-fill diverges from both at the raw level.
  Decision 8 stands (virtual equality is the bar); the proof
  harness's D9 fallback is now EXPECTED to trigger on the
  oversized-backing combo — pre-declare it.
* zstd overlays rebase correctly today (the incompatible bit
  is inert without compressed clusters); wire-15 is a
  today-succeeds→refuses narrowing exactly like commit's D1 —
  accepted, spec-mandated, documented.
* P7 (zero-flag chain clusters) CONFIRMED both shapes:
  `cluster_lookup`'s classic arm ignores bit 0
  (src/crates/qcow2/src/lib.rs:2213-2238) — silent
  active-view corruption; blast radius rebase / convert /
  compare / bench. Pre-existing crates/qcow2 defect, draft
  written, explicitly NOT fixed by this phase; the 5b matrix
  must not use `write -z` seeds.
* Refusal inventory: EVERY cs=512 × divergent × safe combo
  refuses wire 10 (v1 never appends refblocks; not
  byte-idempotent — orphan data appends). These are 5b's
  both-refuse rows and demand scaffolding identity.
* 5b harness constraints: 3-chain rows must use the
  chain-shortening form (rebase-to-mid keeps chains identical
  and copies nothing); new-backing path length must not
  exceed the overlay's existing path slot (wire 8 otherwise).
* P8 exemplar pinned: cs=512, 64M, 512 populated L2 tables,
  identical chains → wire 9 pre-mutation (MAX_STAGED_L2
  count cap); 256 tables succeed.

## Review checklist deltas

Phase-4 deltas apply verbatim (determinism-before-identity,
mismatch = STOP with decoded offsets, no planner/executor source
change, fuzz 0-divergence, baselines pass unmodified, appended
codes only, binary-size lint). Plus:

* The skip probe's outcomes are proven identical to today's on
  every matrix combo (the proof's byte identity covers this
  implicitly; the eviction-exercising test covers it beyond the
  window size explicitly).
* `-u`, unsafe detach and vmdk paths are byte-for-byte untouched
  (diff scoping); safe detach IS migrated and appears in the
  proof matrix.
* The deferred header/path patch group still lands after the
  final refblock write in every emitted program (assert in the
  eviction test: header bytes unchanged until after flush).
* Chain-read behaviour (incl. its wire-12 compressed refusal and
  P7 quirks) is bit-for-bit unchanged — the migration must not
  perturb `read_chain_cluster` or the caches.
* Counters `clusters_copied`/`bytes_copied` identical on the
  proof matrix (stdout identity covers the JSON path).
