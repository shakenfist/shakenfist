# qcow2 write infrastructure — phase 06: migrate bench -w

Parent:
[PLAN-qcow2-write-infrastructure.md](/components/instar/plans/PLAN-qcow2-write-infrastructure/).
Planned at medium effort (the migration pattern is proven twice;
phases 4-5 are the templates). Migrates the bench op's **qcow2
`-w` write path** onto `crates/qcow2-write` +
`crates/qcow2-write-exec`, and **absorbs bench's refcount-growth
planner into the shared crate** (master plan mission item 3 —
the phase's real design work per the phase-5 findings). The read
path, raw `-w`, and the vmdk/vhd/vhdx read support are
untouched.

Grounded in a fresh survey (2026-07-13). Bench is the closest
composition to the crate — it already uses the snapshot-crate
allocator (`alloc_contiguous_clusters_in_refblocks` count=1,
`host_refblocks_start=0`), dense-prefix refblock staging WITH
the contiguity gate, a bounded RT prefix, and wgate codes 1-7
that equal the crate's Gate codes one-to-one by phase-3 design.
But it differs from commit/rebase in three load-bearing ways
that reshape this phase's bar:

1. **Byte identity is NOT the bench bar** (and is unobtainable
   for allocating shapes): bench allocates the data cluster
   FIRST and a fresh L2 SECOND
   (src/operations/bench/src/main.rs:1143 vs :1194); the crate
   allocates L2-first (its proven order, shared with commit and
   rebase). With the shared linear cursor the two host offsets
   swap for every fresh-L2 write, so final images differ for
   allocating schedules. The master plan's mission set bench's
   oracle as compare + check parity from the start; the proof
   relaxes accordingly (decision 7).
2. **fsyncs are real**: bench's image is input slot 0 opened RW,
   so `fsync_input(0)` actually syncs — unlike commit/rebase
   where Durability degraded to a no-op. Today's fsync census:
   1-2 in setup growth, exactly ONE per count-based flush-cadence
   point (:1348), ZERO at end-of-bracket and for
   `flush_interval == 0` runs. A naive plan_flush epoch per
   cadence point would emit up to 4 real fsyncs each — a
   durability AND timing change. Decision 4 preserves today's
   census exactly without any crate change.
3. **The determinism premise holds**: the final image is a pure
   function of (initial bytes, chain content, BenchParams) —
   the schedule is arithmetic, the flush cadence is count-based,
   no clock is readable in the guest write path. stdout is NOT
   fully deterministic (the completion line and three JSON
   fields are wall-clock); the proof harness normalises them
   (decision 7).

## Scope

Deliverables: growth-planner move (6a), the op migration (6b),
`migration-proof.py --op bench` + fuzz soak (6c), docs (6d);
preceded by probes (6p). Sanctioned-change escape hatch as
before: any qcow2-write / qcow2-write-exec change beyond 6a's
scoped module addition stops the phase.

### Settled design decisions (binding)

1. **Growth moves as a pure module, execution stays op-side.**
   `plan_refcount_growth`, `GrowthCaps`, `RefcountGrowthPlan`,
   `GrowthOverflow` (src/crates/bench/src/lib.rs:555-749) move
   verbatim into a new `growth` module of `crates/qcow2-write`,
   with their 13 unit tests and the `guest_caps`-style helper.
   `worst_case_touched` / `TouchedBound` STAY in `crates/bench`
   (they are BenchParams/schedule-coupled — the wrong-direction
   dependency the fact base flagged); bench passes the computed
   `worst_case_new_clusters` number across the boundary. The
   imperative execution (`qcow2_grow_refcounts` — write order,
   the data-first/fsync/header-flip/fsync dance, staged-only
   old-RT free) remains in the bench op, now doing its I/O
   through qcow2-write-exec's byte-range layer. Growth runs
   pre-bracket, BEFORE `new_state` — the WriteState is built
   over the grown staged set, so growth never interacts with
   planner state. Step-program-shaping the growth execution is
   recorded future work (needs nothing new except a staged
   12-byte header source; not worth it while bench is the only
   consumer). Commit/rebase keep their RefcountExhausted
   refusals per OQ6's settled recommendation.
2. **The per-write walk is replaced by planner classification.**
   Bench today re-reads L1/L2 entries from disk per write
   (:1074-1107) — under the crate the staged window is
   authoritative and disk metadata is stale between flushes, so
   the disk walk must GO, not run alongside (D-D). Bench needs
   no skip probe (phase-5 findings: every scheduled offset is
   written deliberately; mapped clusters are Owned overwrites).
3. **Backed fill protocol (D-C): try, refuse, fill, resubmit.**
   For each scheduled window the op submits the window as-is
   (`DataSource::Fill` for whole-pattern windows where
   applicable, else pattern bytes staged in CallerData). If
   plan_write refuses `NeedsBackingFill` (partial allocating
   write, backed image), the op chain-reads the pre-image
   cluster into BUF_DEST exactly as today (:1150-1171), patches
   the pattern window in, and resubmits as a full-cluster
   CallerData write. Decision-9 scaffolding from the refused
   attempt (a fresh L2, if one was allocated) is benign: the
   resubmission classifies against the staged window and
   proceeds — net allocation order (L2 then data) identical to
   a straight full-cluster submission. On UNBACKED images
   partial allocating writes zero-fill in the crate — byte-equal
   to today's chain-fill of zeros, no refusal, no resubmit.
   Owned partial overwrites write in place, as today's fast
   path. The EOV-tail clamp applies as in phases 4-5.
4. **Flush cadence and fsync census preserved exactly, no crate
   change.** All plan_flush calls are driven through a
   fsync-DISABLED `CallTableIo` (`input_fsync = false` — the
   capability flag exists precisely for this; Durability
   degrades to Ordering). At each count-based cadence point the
   op drives a full plan_flush epoch (staged dirty L2s + L1 +
   dirty refblocks reach disk — today they were write-through,
   so the post-fsync durable state is equivalent) and then
   issues exactly ONE `fsync_input(0)` itself, as today;
   `flushes_issued` keeps counting cadence points. End of
   bracket: a final plan_flush epoch with NO fsync (today's
   posture). `flush_interval == 0` runs issue ZERO fsyncs, as
   today. Setup growth keeps its own 1-2 fsyncs op-side.
5. **Carve (tight — verify by const assert).** The WRITE_*
   regions map onto the executor contract nearly 1:1
   (WRITE_RT_BUF → refcount_table, WRITE_REFBLOCKS_BUF →
   refblocks with `min(2048, 2 MiB/cs)`, BUF_DEST →
   bounce/CallerData, WRITE_RMW_BOUNCE → rmw_sector, the
   zero/pattern sectors collapse into fill_sector + a pattern
   sector). Missing and to be carved from the ~2.11 MiB
   headroom: staged L1 (64 KiB), STEP_BUF (64 KiB,
   `[Step; 1365]`), and an L2 window of
   `min(256, WINDOW_BYTES/cs)` slots with ≥1 slot at cs=2 MiB —
   the fact-base arithmetic says this fits only by reclaiming
   WRITE_RB_OFFSETS (16 KiB; plan_flush reads writeback offsets
   from the staged RT, making the side table redundant). If it
   still doesn't fit, alias write-path regions over read-path
   buffers the `-w` runner provably never uses (the rebase
   alias-carve precedent) rather than shrinking anything.
   Bench's cs ≤ 2 MiB envelope is unchanged — this is the first
   guest-side exercise of the crate at 2 MiB clusters (the
   simulation suite already covers it; 6p probes it live).
6. **Wire codes (the template, third application).** ONE
   appended code: `BenchResult::ERROR_IMAGE_INCONSISTENT = 9`
   for the crate's classification refusals with no existing
   bench rendering (UnknownL1Entry, UnknownL2Entry,
   RefcountInconsistent, RefcountCoverage, StagedRegionsMismatch,
   NeedsBackingFill-after-resubmit defensively).
   `RefcountExhausted` keeps `ERROR_ALLOC_EXHAUSTED = 8`.
   `CompressedCluster` keeps today's mid-run gate-2 rendering
   (`ERROR_WRITE_UNSUPPORTED` detail 2), `SnapshotShared` /
   `SnapshotSharedL2Table` keep gate-7 rendering (bench's
   defensive posture). The staging-time contiguity gate KEEPS
   `ERROR_PARSE_FAILED = 3` — it refuses identically today and
   the pure-refactor bar wins over cross-op cosmetic
   unification (recorded as a quirk note). Envelope gates keep
   wgate 1-7 (already identical to the crate's codes);
   `check_envelope` simply replaces the hand-rolled checks with
   the mapping pinned by a test. Planner-protocol errors → the
   internal-bug family (`ERROR_BAD_CONFIG` detail? — 6b picks
   the existing convention and documents it).
7. **Proof buckets (D-A relaxation, pre-declared).**
   `migration-proof.py --op bench` gains bench-specific stdout
   normalisation: assert the header line, the flush line,
   stderr, per-file sha256, and JSON minus
   elapsed-seconds/requests-per-second/bytes-per-second; NEVER
   the completion line. Buckets: (a) read runs and raw `-w`
   (untouched paths, controls) → full identity; (b) qcow2 `-w`
   overwrite-only schedules (pre-populated images, no fresh-L2
   allocation) → full byte identity; (c) qcow2 `-w` allocating
   schedules (incl. growth shapes) → determinism (after ×2
   sha-equal) + `qemu-img compare` content equality
   before-vs-after + `check` clean + normalised info
   equivalence + `flushes_issued` identity + (growth shapes)
   identical RT-geometry deltas — NOT sha equality,
   pre-declared as bucket rules, not exceptions; (d) refusals →
   identical rc/stderr + sha-unchanged where today's refusal is
   pre-mutation (the compressed-after-growth shape is NOT —
   6p probes it and the bucket records reality). Plus a
   post-migration fuzz soak: `--iterations 300 --ops bench`,
   0 divergences (the fuzz oracle is insensitive to D-A by
   construction — verified: it compares the first stdout line,
   `qemu-img compare` and check only).
8. **Zero-flag entries (D-E) become refusals.** Bench today
   blind-allocates over v3 zero-flag L2 entries and chain-fills
   a pre-image the reader mis-handles (#432 territory); the
   crate refuses UnknownL2Entry → new code 9. A
   today-corrupts→refuses narrowing in the phases-4-5 class;
   6p captures today's behaviour as evidence.

### Divergence budget (bench edition)

| # | Divergence | Verdict |
|---|-----------|---------|
| B-D1 | Allocation order swaps (data-first → L2-first) for fresh-L2 writes; final bytes differ on allocating schedules | Accept — bench's oracle is compare+check by mission design; proof bucket (c); NOT a byte-identity phase |
| B-D2 | Fresh-cluster fill I/O shape (one full-cluster write → head-zero + body + tail-zero emission) | Accept — final bytes identical; I/O shape change inside the timed bracket, documented |
| B-D3 | Backed partial allocating writes: chain-fill inline → try/refuse/fill/resubmit protocol | Decision 3; final bytes identical; the COW spot test pins it |
| B-D4 | Metadata write-through per write → staged with flush-epoch writeback | Accept — final bytes identical; mid-run disk state differs (no crash contract; fsync census preserved by decision 4) |
| B-D5 | Classification refusals on exotic entries (zero-flag, reserved bits, refcount inconsistencies) where today blind-allocates | Accept — new code 9; 6p evidence |
| B-D6 | fsync census | Preserved EXACTLY by decision 4 — no divergence |
| B-D7 | Timing character inside the bracket changes (planning overhead, batched metadata writes) | Accept — timing is not comparable across versions by design (no baselines); noted in docs |
| B-D8 | Growth planner relocated; growth behaviour byte-identical (pure move) | 6a proves by test relocation + before/after growth-shape probes |

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6p | medium | default (Fable) | none | Empirical probes on the pre-migration binary (management saves the runnable dist first). No tree changes; scratchpad only. (1) Determinism under load: same qcow2 `-w` argv twice on twins while the host is busy (parallel make in another tree) — sha equality. (2) Fsync census confirmation: strace -e fsync,fdatasync a `--flush-interval` run and an interval-0 run — counts must match the fact base (1 per cadence point + growth's 1-2; 0 otherwise). (3) Backed-overlay fill matrix: overlay `-w` at cs 512/64K with sub-cluster and straddling windows + an unaligned-EOV tail shape — pin exact chain-fill bytes (extends test_qcow2_overlay_write_cow_spot). (4) Growth shapes before-only: the in-place and RT-relocation test shapes — record RT geometry deltas and image shas (the bucket-(c) reference data). (5) Holed-RT bench input (adapted #428 recipe) — confirm today's wire-3 refusal, sha-unchanged. (6) cs=2 MiB `-w` today: allocating + growth-cap boundary shapes. (7) Compressed-after-growth idempotence: a zlib image whose schedule triggers setup growth then hits a compressed cluster mid-bracket — is the refusal sha-idempotent today? (Expected NO — growth wrote first; the refusal-test bucket must record this.) (8) Zero-flag cluster `-w` today (expected mis-fill corruption; #432 evidence — capture for the B-D5 record; issue NOT needed, #432 covers the reader). Report with recipes + a refusal/verdict inventory over the intended 6c matrix. |
| 6a | medium | default (Fable) | none | Move the growth planner per decision 1: new `growth` module in src/crates/qcow2-write (pure — no I/O, no call-table types; the crate's no_std/review bars apply), containing plan_refcount_growth, GrowthCaps, RefcountGrowthPlan, GrowthOverflow and the 13 growth unit tests relocated verbatim (+ the guest_caps helper if it moves cleanly); crates/bench keeps worst_case_touched/TouchedBound and re-exports or imports the moved types so src/operations/bench compiles UNCHANGED in this step (temporary re-export acceptable, removed in 6b). Doc comments cite this plan and PLAN-bench-refcount-growth. make instar / lint / test-rust (quote both crates' lines) / sizes / pre-commit. No behaviour change anywhere — this is a pure code move proven by the test suite relocating green. |
| 6b | high | default (Fable) | none | The op migration per decisions 2-6 and the divergence budget (read this plan, the phase-4/5 plans' 4b/5a rows, both migrated ops as templates, and the 6p findings). Sequential after 6a. Replace the per-write walk + write-through metadata with new_state over the grown staged set + per-scheduled-window plan_write (decision-3 fill protocol; EOV clamp) + exec_window via qcow2-write-exec (fsync-disabled CallTableIo); flush cadence per decision 4 (full epoch + ONE op-side fsync per cadence point; final epoch no fsync); growth execution re-pointed at the crate's moved planner and exec's byte-range layer; carve per decision 5 (reclaim WRITE_RB_OFFSETS; const-assert; alias only if forced, documented); wire code 9 appended + completeness tests + host message (house style); remove 6a's temporary re-exports. The wgate block is replaced by check_envelope + a mapping test pinning gate-code equality. New integration tests: zero-flag refusal (code 9, sha-unchanged), a cs=2 MiB allocating `-w` (compare+check parity vs qemu twin), and a flush-census test (strace or JSON flushes_issued + an fsync counter if observable — at minimum flushes_issued identity on a cadence run). Full existing suite must pass UNCHANGED — especially TestBenchWrite (the COW spot test pins decision 3), TestBenchWriteRefusals (sha-unchanged), TestBenchRefcountGrowth (all 7), TestBenchJson, and the divergence-regression tests. Byte-identity spot checks vs the saved dist: one overwrite-only qcow2 `-w` (bucket b — must be sha-identical), one raw `-w` and one read run (controls), one refusal (sha-unchanged), PLUS one allocating shape checked per bucket (c): determinism, compare, check, flushes_issued. STOP on any bucket violation. make instar / lint / test-rust / sizes / `stestr run test_bench` / pre-commit. |
| 6c | high | default (Fable) | none | Extend scripts/migration-proof.py with `--op bench` per decision 7 (bench stdout/JSON normalisation; buckets a-d pre-declared IN CODE with the 6p-informed refusal inventory; growth shapes carry the RT-geometry-delta assertion). Matrix: cluster sizes {512, 4096, 65536, 2097152} × image sizes {16M, 64M} × {fresh-allocating, pre-populated overwrite-only, growth-triggering, backed-overlay COW} × flush-interval {none, 0, k} × {pattern, straddling bufsize} pruned to ~50 coherent combos + raw/read controls. Run it (before = saved dist, after = 6b HEAD); then `differential-fuzz.py --iterations 300 --ops bench` (0 divergences) and the full bench suite. STOP on bucket violations with decoded evidence. Modify only migration-proof.py; commit-path and rebase-path smoke via --combo filters to prove no regression. |
| 6d | medium | default (Fable) | none | Close-out: quirks.md bench section (code 9, the B-D1 layout change and why it is sound, the flush/durability posture note, zero-flag narrowing, contiguity-gate code note), docs/bench.md updates, CHANGELOG, ARCHITECTURE.md (bench = third consumer; growth module in qcow2-write), master plan execution table + "Findings: phase 6 bench migration" (proof numbers per bucket, the D-A relaxation rationale for posterity, growth-module facts for phase 7's COW work, fsync-census technique, what remains for phases 7-9) + defect-register updates if 6p found anything new, plans index. pre-commit. |

6p first (management saves the dist); 6a after 6p may run
concurrently with nothing (it touches the crate — keep it
exclusive); 6b after 6a; 6c's run after 6b (authoring may
overlap); 6d last. One commit per step minimum; management
reviews and commits.

## Review checklist deltas

Phase-4/5 deltas apply where byte identity applies (buckets a/b/
d); plus:

* Bucket (c) violations are STOPs exactly like identity
  failures: determinism, compare, check, info, flushes_issued,
  RT-geometry — all of them, not a subset.
* The fsync census is verified empirically (strace or
  equivalent), not inferred: cadence runs, interval-0 runs, and
  growth runs all match today's counts.
* The read path, raw `-w`, and all non-qcow2 formats are
  byte-for-byte untouched (diff scoping + the control combos).
* The growth move (6a) is a pure relocation: no arithmetic
  change, tests relocated verbatim and green, bench behaviour
  identical at 6a's commit (the op still passes its whole suite
  before 6b starts).
* `flushes_issued` counts cadence points before and after (the
  JSON test pins it); ExecStats.fsyncs is NOT conflated with it.
* The wgate-to-Gate code-equality mapping is pinned by a test,
  not assumed.
* No qcow2-write/exec changes beyond 6a's scoped growth module.

## Findings: 6p probes (2026-07-13)

All eight 6p probes ran against the pre-migration dist (sha
`f074b4f6…`, built at cde5164). Full report and the 98-row
refusal inventory in scratchpad
(`probes-6p-v2/FINDINGS.txt`, `refusal-inventory.tsv`); 6d folds
the durable facts into quirks.md. Load-bearing results:

1. **Determinism holds under host load** for all five schedule
   classes (fresh-alloc, growth-reloc, overwrite-only,
   overlay-COW, cadence) — so `after-x2-sha-equal` is a valid
   bucket-(c) check. *Fixture caveat:* twin/overlay fixtures must
   share ONE backing path, or the embedded backing-filename
   string defeats sha comparison (bit us on the first overlay
   attempt).
2. **Fsync census matches decision 4 exactly:** 1 fsync per
   cadence point; growth = 1 (in-place) / 2 (relocation); 0 at
   end-of-bracket and for `--flush-interval 0`. Confirmed by
   strace. `flushes_issued` counts ONLY cadence points, never
   growth fsyncs (e.g. relocation growth: `flushes_issued`=0 vs
   strace fsyncs=2). 6b's flush-census test must keep the split:
   `flushes_issued == total_flushes(count,interval)` and (if
   counting) `fsyncs == flushes_issued + growth_fsyncs(0|1|2)`.
3. **Backed-overlay COW fill bytes pinned** (sub-cluster,
   straddle, EOV-tail). EOV clamp zero-fills past virtual size;
   `==modulus` offset wraps to 0 (worst_case_touched bound-2
   treats it as wrap) — 6c fixtures must account for it. The
   migrated try/refuse/fill/resubmit protocol must reproduce
   these exact host-cluster layouts.
4. **Growth RT-geometry deltas** captured for the two modes
   (in-place: RT offset/clusters unchanged; relocation: both
   change) — these are the bucket-(c) reference deltas 6c asserts.
5. **Holed-RT** input: wire-3 (`ERROR_PARSE_FAILED`) refusal,
   sha-UNCHANGED (pre-mutation). Bucket (d), sha-stable.
6. **cs = 2 MiB** is a clean allocating success but has **no
   growth path in-envelope** (one refblock covers 2 TiB) and the
   bench bufsize cap == qcow2 format max (never spuriously
   refuses). 6c has no 2 MiB growth combo to assert; carve
   const-asserts must still keep ≥1 L2 slot at 2 MiB.
7. **Compressed-after-growth is NOT sha-idempotent under
   refusal:** zlib is not an incompatible-features header bit, so
   the setup compression gate does not fire; preemptive growth
   mutates the file, then the per-cluster run refuses at gate 2 →
   sha changed. 6c refusal bucket (d) must pre-declare this combo
   as recorded-not-sha-stable (verdict = rc/stderr identical +
   check-clean, NOT sha-idempotent). The no-growth zlib control
   IS sha-stable.
8. **Zero-flag (#432):** decision-8's `UnknownL2Entry`→code-9
   narrowing closes only the TARGET-side zero-flag entry
   (Variant A); a zero flag in a BACKING cluster reached via the
   COW read (Variant B) still mis-fills post-migration — #432 is
   a read-path defect (crates/qcow2 cluster_lookup), unfixed by
   phase 6. The B-D5 record must NOT over-claim that decision-8
   eliminates zero-flag corruption for backed images.

### New defect #433 — fixed pre-6b (decision: fix-first)

6p found a previously-unfiled defect (filed as
[#433](https://github.com/shakenfist/instar/issues/433)): an
**overwrite-dominant schedule that also crosses the preemptive
growth threshold** provisions refcount blocks and writes their RT
pointers, but the run allocates nothing, so
`flush_dirty_refblocks` (writes only DIRTY blocks) never
materializes them — the RT ends up referencing refcount blocks
past EOF. Silent (exit 0), `qemu-img check`-dirty on a
check-clean input; repairable by `check -r` but a later allocator
could double-allocate. Not covered by #427–#432.

Per the fix-first decision, the root-cause fix (materialize every
RT-referenced refblock during growth — restores qemu's
allocated-blocks invariant, rides the existing growth fsync so the
census is unchanged) lands as a **standalone commit on the current
bench code before 6b**, with a regression test for the
overwrite-only ∩ growth corner. Consequences for the rest of
phase 6:

* **The before-dist is rebuilt at the fix commit** (not cde5164);
  6b's migration proof is before-FIXED vs after-migrated.
* **The overwrite-only ∩ growth corner is check-clean after the
  fix, so it is NOT quarantined** — it becomes a normal proof
  combo. Because the run is overwrite-only (no data allocation),
  B-D1's alloc-order swap does not apply, so it is expected
  byte-IDENTICAL across the migration (bucket b, with the growth
  metadata included), and check-clean (bucket c assertions hold).
* 6b migrates the CORRECTED growth execution as the "byte-identical
  pure move" (B-D8) — the fix is inside the code B-D8 covers, so
  faithful migration preserves the fix.

### Bucket mapping (6p-informed, for 6c's in-code buckets)

* **(a) controls** — raw `-w`, read runs, cs=2 MiB cap-boundary
  success: full byte identity across versions.
* **(b) overwrite-only (no growth, or post-#433-fix
  overwrite∩growth)** — byte-identical across versions.
* **(c) allocating** (fresh + growth + overlay-COW): determinism
  + `qemu-img compare` content-equal + check-clean + normalised
  info + `flushes_issued` identity + (growth) identical
  RT-geometry deltas. NOT sha-equal across versions (B-D1
  alloc-order swap).
* **(d) refusals**: identical rc/stderr + sha-unchanged where
  today's refusal is pre-mutation (holed-RT wire-3;
  compressed-no-growth gate-2). Compressed-AFTER-growth gate-2 is
  recorded-not-sha-stable. Zero-flag → post-migration code-9
  refusal (Variant A) or persistent #432 read-path corruption
  (Variant B).
