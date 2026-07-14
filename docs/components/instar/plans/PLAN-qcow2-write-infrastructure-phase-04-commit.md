# qcow2 write infrastructure — phase 04: migrate commit

Parent:
[PLAN-qcow2-write-infrastructure.md](/components/instar/plans/PLAN-qcow2-write-infrastructure/).
Planned at high effort (the first migration is the template for
phases 5-6). Migrates the qcow2 path of `instar commit` onto
`crates/qcow2-write`, replacing the op's inlined per-cluster
allocate-on-write composition with plan_write / plan_flush driven
through a new reusable step executor. The bar, from the master
plan: a pure refactor from the outside — the full existing test
surface (unit, integration, baselines, differential fuzz) passes
unchanged, plus the phase-1 Q3 migration-proof oracle:
instar-before vs instar-after byte identity over a deterministic
fixture matrix, with instar's own run-to-run determinism asserted
first as the premise.

Everything below is grounded in a fresh survey of the commit op,
the executor substrate and the allocator (2026-07-12). Load-
bearing verified facts: commit's `allocate_backing_cluster_qcow2`
(src/crates/commit/src/qcow2.rs:219-280) and
`snapshot::qcow2::alloc_cluster_in_refblocks` driven by
qcow2-write's `alloc_cluster` are mechanically identical for
count==1 — same first-fit flat scan from a persistent (0,0)
cursor, same claim-time staged refcount=1 write, same
one-past-claim cursor advance, same `flat_index * cluster_size`
result — and both compositions allocate a fresh L2 before the
fresh data cluster within an iteration
(src/operations/commit/src/main.rs:754,791 vs qcow2-write's
SlotFree→FreshAlloc preceding SlotReady→DataAlloc). Driving
plan_write per overlay cluster in commit's existing loop order
therefore yields the same host offsets, values and file growth —
byte identity is achievable, not aspirational.

## Scope

Three deliverables:

1. **`src/crates/qcow2-write-exec/`** — the guest-side step
   executor, shared by phases 5-6. A `no_std` crate (precedent:
   `crates/qcow2` already holds CallTable-consuming guest helpers
   linked by four ops and stays host-unit-testable with mock call
   tables, src/crates/qcow2/src/lib.rs:4574-4908).
2. **The commit op migration** — the backing-side write
   composition replaced; overlay walk/reads, the overlay-clear
   pass, cross-image gates, the defensive header re-read, result
   accounting and the whole vmdk path untouched.
3. **`scripts/migration-proof.py`** — the reusable
   before/after harness (re-parameterised for phases 5-6), plus
   the recorded proof run.

Out of scope: COW (phase 7), refcount growth (phase 6),
`fsync_output` (recorded future work; barriers degrade), any
rebase/bench change, any qcow2-write crate change beyond what a
genuine defect found by this phase would force (stop and report
to the management session first).

### Settled design decisions (binding)

1. **Executor crate, not per-op code.** `qcow2-write-exec`
   depends on `shared` + `qcow2-write` only. It owns: (a) the
   RegionId → caller-carved slice mapping (a `Regions` struct of
   `&mut [u8]` built by the op from scratch addresses — never
   `static`, .bss hazard class); (b) the TargetDevice → call-
   table mapping (Input0 → `read/write_input_sector` +
   `fsync_input(0)`; Output → `read/write_output_sector`, no
   fsync); (c) a byte-range layer over the strictly
   sector-addressed call table — sector split plus sub-sector
   RMW through a caller-provided per-device bounce sector (the
   layer commit/rebase/bench/bitmap each hand-roll today, e.g.
   src/operations/commit/src/main.rs:218-257); (d) fill
   synthesis for `ZeroRange`/`FillRange` via a caller-provided
   fill sector (bench's `WRITE_ZERO_SECTOR` pattern, bench
   main.rs:138-146); (e) barrier policy — all guest I/O is
   synchronous (issue order == completion order,
   src/core/src/virtio.rs:244-342), so `Ordering` is a no-op
   and `Durability` maps to `fsync_input(0)` on Input0 and
   degrades to `Ordering` on Output, per qcow2-write decision 4.
   The executor is a literal interpreter of the `StepKind` doc
   contracts; it contains zero planning logic. Host-unit-tested
   with a mock CallTable (sector-size 512 and 4096 variants,
   sub-sector RMW proven byte-exact).
3. **Driving loop contract.** The op loops per qcow2-write's
   decision-1 protocol: plan into a scratch-carved 64 KiB step
   buffer (~1365 steps), on `Ok` or `BufFull` execute the
   window, reset, resume (`BufFull` is also the LoadCluster
   window boundary); identical arguments on resume
   (`ResumeMismatch` enforces this). One plan_flush epoch at the
   end of the cluster loop, replacing commit's inline L2 → L1 →
   refblocks flush (main.rs:814-861) — same final bytes, same
   granularity (whole clusters / whole L1), refcounts last.
4. **Staging model: single-copy, dense-prefix, contiguity-
   gated.** The migrated op stages backing refblocks per
   `StagedRegions`' dense-prefix-from-host-cluster-0 contract
   and gates on RT contiguity exactly like bench/bitmap
   (src/operations/bench/src/main.rs:332-334, bitmap
   main.rs:343-367). Commit today compacts non-zero RT entries
   and indexes them as if dense (main.rs:617-643 +
   crates/commit qcow2.rs:260-266) — on a sparse RT it silently
   computes wrong host offsets; the gate turns that latent
   misallocation into a typed refusal (divergence D4, accepted).
   The 3 MiB `PLANNER_SCRATCH` duplicate refblock copy
   (crates/commit qcow2.rs:173-176) is reclaimed — the planner
   mutates the single staged copy in place (decision 6). New
   backing-side carve (const-asserted, fixed regions only —
   nothing grows at runtime, but keep the c84743e ordering
   discipline anyway): staged L1, RT, refblocks, L2 window
   (slots = min(256, window_bytes / cluster_size)), step
   buffer, bounce cluster, fill + RMW sectors. Overlay-side
   staging is untouched.
5. **Capacity envelope: backing-side caps widen deliberately.**
   Commit's `MAX_REFBLOCKS = 32` and stage-everything backing-L2
   cap (min(256, 2 MiB/cs)) disappear: the L2 window evicts
   (no total-count refusal at all) and refblocks are bounded by
   staged bytes and `MAX_REFBLOCKS = 2048`. Strictly more images
   succeed; nothing in the test surface pins the old
   `ERROR_SCRATCH_TOO_SMALL` shapes (verified: fuzz and
   baselines stay ≤ 64M / ≤ 64K-cluster shapes; the pinned
   refusal tests are host-CLI-level). Overlay-side caps are
   unchanged, so overlay-bound shapes refuse exactly as today.
   The proof harness probes the widening explicitly.
   **Amended after 4p:** every refusal on the Q3 matrix itself
   is wire-11 refcount exhaustion (which decision 6 keeps —
   phase 6 lifts it), so the refuse-before/succeed-after bucket
   is empty ON the matrix; the widening probe uses 4p's
   verified off-matrix exemplar (cs=512, 16M backing with 8M
   seeded → 66 populated RT entries > 32 → wire-10 refusal
   today, pre-mutation) which must succeed post-migration with
   qemu info/check parity.
6. **Wire-code mapping (append-only).** `RefcountExhausted`
   keeps wire 11. Compressed backing L2 entries map to the
   existing `ERROR_UNSUPPORTED_FORMAT` (the code the overlay
   side already uses for compressed entries, main.rs:718).
   Envelope `Gate`s that duplicate existing gates keep their
   existing codes; the two genuinely new refusal families get
   ONE new appended `CommitResult` code each:
   `ERROR_BACKING_UNSUPPORTED = 16` (Gate::UnknownIncompatible —
   zstd bit / unknown incompatible bits; spec-mandated, commit
   today illegally proceeds) and
   `ERROR_BACKING_INCONSISTENT = 17` (classification refusals on
   inconsistent-but-gate-passing images: SnapshotShared /
   RefcountInconsistent / RefcountCoverage / UnknownL1Entry /
   UnknownL2Entry / StagedRegionsMismatch / non-contiguous RT).
   Host messages in the house style; exhaustive code lists and
   completeness tests updated (src/shared/src/lib.rs:4221ff).
7. **The unaligned-virtual-size tail cluster (D9).** Commit
   today always writes whole clusters, including past an
   unaligned `virtual_size`; plan_write bounds-checks against
   `virtual_size` and would refuse. The op clamps the tail
   request to `virtual_size - voff`. Consequences: on a fresh
   allocation the crate zero-fills the tail (byte-equal to
   today's behaviour in practice — overlay tail bytes past EOV
   are zeros); on an owned overwrite the backing's old tail
   bytes past EOV survive where today they are overwritten.
   Virtual content is identical either way. The proof matrix
   includes an unaligned size; if raw sha256 differs there, the
   harness falls back to virtual-content equality (qemu-img
   convert both, compare) and the divergence is recorded as
   accepted with this rationale. **Amended after 4p:** the
   probes showed the simple unaligned case is byte-identical
   under both tools (tail bytes past EOV are always zeros in
   stock fixtures), but the chained-backing variant is a real
   capability regression — today's instar AND qemu both handle
   it, while the clamped tail write (`win_len != cluster_size`)
   would hit `NeedsBackingFill`. Management decision: amend
   qcow2-write (step 4q, the one sanctioned crate change) so a
   partial allocating write whose coverage reaches
   `virtual_size` (the tail cluster) classifies as full
   coverage — bytes beyond EOV are not virtual content, so the
   zero-fill is the correct pre-image regardless of backing
   (and matches qemu's observed tail behaviour). The
   NeedsBackingFill refusal remains for every genuinely partial
   write below EOV.
8. **crates/commit slims down.** Deleted as superseded:
   `allocate_backing_cluster_qcow2`, `BackingAllocationState`,
   the planner's refblock copy + dirty bitmap
   (`Qcow2CommitContext` reduced to the overlay/cross-image
   geometry the op still needs), the dead `CommitPatch` /
   `CommitPlan` machinery (crates/commit/src/lib.rs:159-211,
   never emits patches), and their unit tests (superseded by
   qcow2-write's + the executor's). Kept: all cross-image
   validation, overlay geometry (`overlay_cluster_count`,
   `overlay_entries_per_refblock`), the exported byte-offset
   helpers, and the entire vmdk module untouched.

### Divergence budget (accepted, tested, documented)

Verified divergences between today's composition and the crate;
each is accepted with the stated rationale, covered by a test in
4b/4c, and documented in 4d. Anything found beyond this list
stops the phase and comes back to the management session.

| # | Divergence | Verdict |
|---|-----------|---------|
| D1 | `check_envelope` refuses zstd / unknown incompatible bits on the backing; commit today proceeds (spec violation) | Accept — spec-mandated narrowing; new code 16; refusal test |
| D2 | Compressed backing L2 entries: today's loop overwrites the masked offset in place — **live corruption, sibling of #420** (main.rs:788-790); crate refuses `CompressedCluster` | Accept — the migration IS the interim fix; probe first (4p), issue drafted for user approval; refusal test with sha256-unchanged |
| D3 | COPIED / refcount / reserved-bit classification refusals on images that pass the nb_snapshots gates but are internally inconsistent; today reused as-is | Accept — unreachable on check-clean images; new code 17 |
| D4 | Sparse (non-contiguous) refcount table: today silent misallocation (wrong host offsets); crate + contiguity gate refuse | Accept — **4p upgraded this to a LIVE defect**: holed RTs are stock-producible (discard + `qemu-img resize --shrink`), pass `qemu-img check` clean, and today's commit corrupts them (2654 check errors / 69 leaked clusters in the probe). Code 17; refusal test built from the 4p recipe; issue draft awaiting user approval |
| D5 | L2 staging becomes lazy (LoadCluster) with eviction; flush order by first-touch slot, not staged-array order; final bytes identical, intermediate I/O order differs | Accept — no crash contract exists for commit today (zero fsyncs); byte-identity proof is the arbiter |
| D6 | Backing-side capacity caps widen (decision 5) | Accept — deliberate, probed by 4c |
| D7 | Refusal wire codes for new families | Decision 6's mapping |
| D8 | plan_flush emits Durability barriers; executor degrades to Ordering on Output | Accept — behaviourally a no-op (zero fsyncs today, preserved) |
| D9 | Unaligned-virtual-size tail cluster | Decision 7's clamp + probe |

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4p | medium | default (Fable) | none | Empirical probes, no tree changes, findings as text. (1) D2: build a qcow2 backing with compressed clusters (`qemu-img convert -c`) plus a normal overlay; run today's `instar commit` and `qemu-img commit` on twins; capture whether instar corrupts (sha256 + `qemu-img check` + content read-back before/after) and what qemu does. If corruption confirmed, draft a GitHub issue in the scratchpad for the user's approval (do NOT file it). (2) D9: unaligned `virtual_size` (e.g. 1M+512) twins through both tools — record tail-byte and virtual-content behaviour; also the chained-backing variant (backing has its own backing). (3) D6: empirically inventory which phase-1 Q3 matrix combos refuse on today's binary and why (overlay cap vs backing cap) — parameterises 4c's bucketing. (4) D4: verify sparse-RT reachability (can qemu-img produce one? discard paths?); if trivially producible, capture today's misallocation on a synthetic image. Report with exact recipes. |
| 4a | high | default (Fable) | none | Build `src/crates/qcow2-write-exec/` per settled decisions 1-2 (read them; read qcow2-write's StepKind/StagedRegions/BarrierClass doc contracts and the mock-CallTable test precedent in crates/qcow2). Workspace registration. API sketch: `Regions<'a>` (per-RegionId `&mut [u8]` slices + per-device RMW bounce + fill sector), `Devices` (call-table fns + sector sizes + fsync capability per TargetDevice), `execute(steps: &[Step], regions, devices) -> Result<(), ExecError>` applying each step literally per its doc contract, byte-range layer with sub-sector RMW, barrier policy per decision 2(e). Host unit tests with a mock CallTable: every StepKind, sector sizes 512/4096, sub-sector RMW byte-exactness vs a reference model, Durability-on-Output degradation, fsync-refusal surfacing. `make instar`, `make lint`, `make test-rust` (quote the new crate's lines), `scripts/check-binary-sizes.sh`, `pre-commit run --all-files`. No commit; management reviews. |
| 4q | high | default (Fable) | none | The one sanctioned qcow2-write change (decision 7 as amended): in `plan_write`'s Unallocated classification, a partial write whose coverage reaches `virtual_size` (the tail cluster of an unaligned image) classifies as full coverage — allocate, write the covered bytes, zero-fill only the beyond-EOV remainder, no `NeedsBackingFill`. Genuinely partial writes below EOV keep the refusal. Unit tests (backed + backing-less tail shapes, both classifications on either side of the boundary); extend tests/ordering.rs and tests/simulation.rs with an unaligned-virtual-size configuration (the SimDisk content model must treat beyond-EOV bytes as outside virtual content). Full bar: make instar/lint/test-rust (quote qcow2-write lines), check-binary-sizes, pre-commit. |
| 4b | high | default (Fable) | none | Migrate the commit op per settled decisions 3-8 (read the whole plan first, plus the master plan's phase-3 findings and this phase's fact anchors). Sequential after 4a. Replace the backing-side composition (staging main.rs:582-661, planner ctx use, per-cluster backing half 740-812, flush 814-861) with: dense-prefix + contiguity-gated staging into the re-carved layout, `check_envelope` + `new_state` + per-overlay-cluster `plan_write` (full clusters; tail clamped per decision 7) + windowed execution via qcow2-write-exec + final `plan_flush`. Keep: gates order and codes, overlay walk/skip/read, DATA_BUF as CallerData region, overlay-clear pass, header re-read, result counters, vmdk untouched. Wire codes per decision 6 incl. host messages + completeness tests. Slim crates/commit per decision 8. On a non-BufFull planner error mid-request, execute the already-emitted window BEFORE surfacing the error — that reproduces today's bytes-written-up-to-the-refusal behaviour, keeping even refusal paths byte-identical to the pre-migration binary. New integration tests: D1/D2/D4 refusals (D2 and D4 fixtures per the 4p recipes; sha256-unchanged where the refusal is pre-mutation), D6 widening success shape (4p's off-matrix exemplar). Full bar: `make instar`, `make lint`, sizes, `make test-rust`, commit integration suite (`cd tests && .venv/bin/stestr run test_commit`), `pre-commit run --all-files`. Stop and report if byte-identity spot checks fail (one 64K and one 512-cluster fixture against the pre-migration binary — the management session saves one to the scratchpad before this step starts, the phase-2 `instar-before-2d` precedent). No commit. |
| 4c | high | default (Fable) | none | `scripts/migration-proof.py` (may be authored concurrently with 4b; final run strictly after 4b lands). Reusable across phases 5-6: `--op commit --before <binary> --after <binary> [--matrix ...]`. Per the master plan Q3 finding: deterministic fixture matrix built fresh each run with the pinned qemu-img (cluster_size {512, 4096, 65536} × size {1M, 64M} × seed {empty, 64K, multi-extent} × lazy_refcounts {off, on} × {implicit, explicit -b} + one unaligned-size case per decision 7). For each combo: run the after-binary TWICE on twin fixtures (run-to-run determinism: sha256s equal — the premise); then before vs after on twins. Bucketing: both-succeed → sha256 equality of every mutated file + stdout; refuse-before/succeed-after → must match 4p's D6 inventory, assert qemu info/check parity; both-refuse → identical exit code + stderr, AND byte-identity of the mutated files even so — 4p showed the wire-11 shapes mutate the backing (~124 KiB of orphaned committed data) before refusing, and the allocator equivalence predicts identical scaffolding bytes from both binaries; a mismatch here is a STOP, not an exclusion; D9 fallback per decision 7 (expected unnecessary after 4q — the probes showed byte-identical output on the simple unaligned shape). Python: single quotes, 120-char lines. Then the full remaining surface: baseline matrix (`stestr run test_commit` incl. TestCommitBaselineMatrix), `scripts/differential-fuzz.py --iterations 300 --ops commit`, and `make test-rust`. Record everything; findings text back. No commit of results — the script commits, the numbers go to the findings. |
| 4d | medium | default (Fable) | none | Close-out: quirks.md (widened backing caps, new refusal families 16/17, compressed-backing and sparse-RT behaviour change, D9 note), docs/commit.md refusal table, CHANGELOG (migration entry: byte-invisible on the proof matrix, capability widenings called out), master plan execution table phase 4 → Complete + "Findings: phase 4 commit migration" section (proof numbers, divergence-budget outcomes, defect issue status, executor facts phases 5-6 reuse), plans index, ARCHITECTURE.md qcow2-write section gains the executor crate. `pre-commit run --all-files`. |

4p runs first (its findings gate 4b's test list and 4c's
bucketing) and may run concurrently with 4a. 4q follows 4a
(sequential — both touch the qcow2-write test files' vicinity);
4b follows 4q; 4c's run follows 4b; 4d last. One commit per
step minimum, management session reviews and commits.

4p outcome note (2026-07-12): D2 corruption confirmed (silent,
multi-cluster blast radius) and D4 upgraded to a live defect;
issue drafts for both are in the session scratchpad awaiting
the user's decision — filing is never done without approval.

## Review checklist deltas

Standard checklist from PLAN-TEMPLATE.md, plus:

* The migration-proof harness's determinism assertion passed
  BEFORE the before/after comparison is trusted; every matrix
  combo is accounted for in exactly one bucket, and the
  refuse-before/succeed-after bucket matches 4p's inventory
  exactly (no unexplained envelope change in either direction).
* Byte-identity failures are STOP conditions, not
  tune-until-green: any mismatch outside the D9 rationale comes
  back to the management session with the differing offsets
  decoded (qcow2 structure at the diff site).
* No qcow2-write (planner) source change slips in; the executor
  crate contains zero planning decisions (grep for
  classification/allocation logic — none).
* The overlay-clear pass, vmdk path and header re-read are
  byte-for-byte untouched (git diff scoped to the qcow2 backing
  half of the op).
* The commit fuzz run is 0-divergence and the baseline matrix
  passes against unmodified testdata (info-equivalence — the
  migration must not need baseline regeneration; needing it
  means the migration is not byte-invisible and is a STOP).
* New wire codes appended (16, 17), never reordered; overlay
  clear-pass counters unchanged; `CommitResult` completeness
  tests know the new codes.
* Binary-size lint passes with the op now linking two more
  crates (headroom is large: commit is 25 KiB of 768 KiB
  today).
* The D2 issue draft (if corruption confirmed) is left in the
  scratchpad for the user — never filed without approval.
