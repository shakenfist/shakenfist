# qcow2 write infrastructure — phase 09: docs close-out

Parent:
[PLAN-qcow2-write-infrastructure.md](/components/instar/plans/PLAN-qcow2-write-infrastructure/).
Planned at **medium effort** (the master plan's tier for phases 2/8/9 —
a docs pass, no code). The **final** phase of the programme: fold in the
remaining `docs/qcow2/` write-infrastructure implementation notes,
consolidate the per-op docs against the shared infrastructure, and close
out the whole nine-phase programme (master plan → complete, a stable
maintainer reference, final acceptance).

Grounded in a fresh survey (2026-07-14) of the doc landscape: the
per-phase docs are already written — 7f wrote the COW quirks + op-doc
snapshot behaviour + the phase-7 findings + defect register; 8c wrote
the phase-8 fuzzing docs (ARCHITECTURE, docs/testing.md, CHANGELOG,
findings). So phase 9 is NOT another per-phase pass — it is the
cross-cutting close-out the earlier phases deferred.

## What is different about phase 9 (why it's small and focused)

Phase 9 writes the **durable, forward-looking reference** for the
finished write infrastructure — the "how it works now" a future
maintainer reads, distinct from the plan files' "how we got here"
(which stay as the historical record with their per-phase findings). The
per-phase docs already cover the incremental story; phase 9 distils the
end state into one coherent implementation-notes doc, points the op docs
at it, and marks the programme complete. No behaviour, no crate, no test
change.

## Grounding: the doc landscape (survey)

* **`docs/qcow2/` already exists** with seven docs — qcow2-format.md,
  qcow2-l1l2-tables.md, qcow2-refcount.md, qcow2-snapshots.md,
  qcow2-compression.md, qcow2-encryption.md, and
  **qcow2-implementation-notes.md**. The implementation-notes doc is
  **read/format-focused** (byte order, v2/v3 differences, cluster
  alignment, reserved bits, zero-vs-unallocated, backing chain, L2
  caching, compressed clusters, consistency checking, qemu limits). **No
  qcow2/ doc covers the WRITE planner, copy-on-write, or refcount
  growth** — that is the gap phase 9 fills. (qcow2-refcount.md /
  qcow2-snapshots.md touch refcounts/snapshots from the read/format
  angle; the write-side COW machinery is undocumented there.)
* **Per-op docs** (docs/commit.md, docs/rebase.md, docs/bench.md) each
  now describe their own snapshot/COW behaviour (7f) but re-explain the
  shared write path independently — a consolidation opportunity (link a
  single reference).
* **ARCHITECTURE.md** has a `qcow2` format section (~:864), Differential
  Fuzzing (~:1044) and Coverage-Guided Fuzzing (~:1143) sections (8c
  updated the latter two) but **no dedicated "qcow2 write infrastructure"
  architecture subsection** tying the crate boundary + planner/executor +
  COW + growth together.
* **The master plan** carries the full per-phase findings (phases 1-8)
  and the defect register; its execution table has row 9 "Not started"
  and phases 1-8 Complete. Phase 9 marks row 9 Complete and adds the
  programme retrospective.
* **The plan files** (PLAN-qcow2-write-infrastructure*.md) are the
  historical planning record — leave them; the new docs/qcow2 reference
  is the non-historical "current state" doc.

## Scope

Deliverables: the write-infrastructure implementation-notes reference +
per-op consolidation (9a), and the programme close-out + final
acceptance (9b). Out of scope: any code/test change; rewriting the plan
files (historical record); re-running the full KVM suites/soaks from
scratch (they passed at each phase HEAD and the tree is docs-only since —
9b does a light build/unit acceptance + a representative confirmation,
not a from-zero re-verification).

### Settled design decisions (binding)

1. **One new reference doc, not a sprawl.** Author a single
   `docs/qcow2/qcow2-write-planner.md` (name at 9a's discretion; this is
   the recommendation) as the stable implementation-notes reference for
   the whole write infrastructure. It cross-links the existing qcow2/
   docs (format, l1l2, refcount, snapshots) rather than duplicating them,
   and cites the plan files for the historical detail. If a section grows
   unwieldy it MAY split (e.g. a separate copy-on-write.md), but default
   to one cohesive doc.
2. **The reference is maintainer-facing "current state," not a
   changelog.** It documents how the code works today, keyed to the crate
   API and the invariants — not the phase-by-phase evolution (that lives
   in the plan findings). Present tense, no "phase 4 did X" narration in
   the body (a short provenance footnote pointing at the plan is fine).
3. **Consolidate, don't duplicate, the op docs.** commit/rebase/bench
   docs keep their op-specific behaviour (device, oracle, snapshot-view
   semantic) but link the shared reference for the common write path
   instead of each re-deriving it. Do not churn beyond that.
4. **Plan files stay; the master plan gets the retrospective.** The
   master plan's execution table → all 9 Complete, plus a short
   "Programme retrospective / final state" section (what shipped, the
   resolved defects #420/#421/#423/#432, the two documented follow-ups,
   the durability/oracle posture) and a final defect-register state. The
   per-phase findings sections stay as-is.
5. **Record the open follow-ups in the durable docs, not just the plan.**
   The two non-corrupting follow-ups (commit's overlay non-emptying for
   snapshot-bearing overlays / the overlay-side COW-clear primitive; the
   tighter rebase growth bound) and the minor debug-only growth-guard
   note belong in quirks.md and/or the new reference so they survive
   outside the planning history. (They are already in the plan; surface
   them in the reference's "known limitations / future work".)
6. **Final acceptance is a light gate, not a from-zero re-run.** The
   integration suites (commit/rebase/bench + cow-cross-version) and the
   fuzz soaks passed at their phase HEADs and nothing behavioural changed
   since. 9b confirms the tree still builds and unit-tests clean
   (`make instar` / `make lint` / `make test-rust` / check-binary-sizes /
   `pre-commit`) and re-runs a REPRESENTATIVE subset (one snapshot-COW
   integration test per op + a short differential soak) as a smoke — not
   the full matrices.

## The write-infrastructure reference (9a) — required coverage

The new doc must cover, at reference depth (linking the crate API):

* **The problem & the crate boundary** — why a shared write planner
  (`crates/qcow2-write`) + executor (`crates/qcow2-write-exec`), and what
  each owns (pure planning math vs call-table I/O). The three consumers
  (commit / rebase safe mode / bench `-w`) and what each supplies
  (device, staged buffers).
* **The step-program ABI** — the windowed `StepKind` vocabulary
  (ReadCluster / WriteRange / ZeroRange / FillRange / PatchEntryU64 /
  LoadCluster / WritebackCluster / ZeroRegion / Barrier), RegionId, the
  `BufFull` resume protocol and the fixed step buffer.
* **The write envelope & classification** — the gates (refcount_bits==16,
  no extended-L2 / compression / encryption / external-data,
  dirty/corrupt refused, the COW-gated HasSnapshots) and the per-cluster
  classification (Owned overwrite / Unallocated allocate / SnapshotShared
  COW / the refusal families), and the zero-flag target policy.
* **Allocate-on-write** — the L2-first ordering, the snapshot-crate
  allocator, sub-cluster RMW, the EOV clamp, the backing-fill protocol.
* **Copy-on-write** — data-cluster COW (copy, repoint, rc+1 new / −1 old)
  and L2-table COW (copy, repoint L1, **no child-refcount change** —
  children already rc≥2 from snapshot creation; the anti-corruption
  invariant `max_rc < 3`), nested COW, the decrement primitive, and the
  per-op snapshot-view semantic (commit preserves / rebase reads through
  the new backing — the "== qemu twin" invariant).
* **Refcount growth** — `plan_refcount_growth` (placement math, shared)
  and the op-side execution (`qcow2-write-exec::growth`), the #433
  materialize-every-provisioned-refblock invariant, the fsync census.
* **The crash-ordering contract** — data durable before the pointer that
  reaches it; refcounts flushed last; the durability barriers and their
  degrade-to-Ordering posture on fsync-less devices.
* **Verification posture** — the migration-proof model (phases 4-6
  byte-identity) vs the qemu-parity model (phase 7 COW: check + compare +
  the snapshot read-back oracle), and the fuzz coverage (the crate `sim`
  target + the snapshot-bearing differential coverage).
* **Known limitations / future work** — the two follow-ups + the
  debug-guard note (decision 5); the byte-parity non-goal (C11); growth
  not yet adopted by other mutators.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 9a | medium | default | none | Author `docs/qcow2/qcow2-write-planner.md` — the stable implementation-notes reference for the write infrastructure, covering everything in "The write-infrastructure reference" section above, at maintainer-reference depth, present tense, cross-linking the existing docs/qcow2/ docs and citing the plan files for history (decisions 1/2/5). Cross-check every claim against the code (the crate API, the op call sites, the invariants) — do NOT invent. Consolidate docs/commit.md / docs/rebase.md / docs/bench.md to LINK the new reference for the shared write path while keeping their op-specific behaviour (decision 3). Add a short "qcow2 write infrastructure" subsection (or paragraph) to ARCHITECTURE.md's qcow2 section pointing at the reference + naming the crate boundary. Update docs/index.md and docs/qcow2 cross-links if they enumerate the qcow2 docs. Surface the known limitations / follow-ups in docs/quirks.md if not already there (decision 5). pre-commit. Docs only; do NOT touch src/tests/scripts; do NOT commit. |
| 9b | medium | default | none | Programme close-out + final acceptance. Master plan (docs/plans/PLAN-qcow2-write-infrastructure.md): execution table row 9 → Complete with the 9a/9b SHAs; add a "### Programme retrospective / final state" section (the nine phases in one paragraph each or a table; the shipped capability — shared qcow2 write planner + COW + growth across commit/rebase/bench; the resolved defects #420/#421/#423/#432 and #422's phase-2 fix; the two follow-ups + the debug-guard note; the durability + qemu-parity posture); confirm the Success Criteria checklist is all met (tick each, noting where verified). docs/plans/index.md: the whole qcow2-write-infrastructure programme → complete. CHANGELOG.md: a final consolidated programme entry if the per-phase entries do not already read coherently (else leave — do not duplicate). Final acceptance (decision 6): run `make instar` / `make lint` / `make test-rust` / `scripts/check-binary-sizes.sh` / `pre-commit` (all clean) and a REPRESENTATIVE smoke — one snapshot-COW integration test per op (`stestr run TestCommitSnapshotCow TestRebaseSnapshotCow TestBenchSnapshotCow` or equivalent) + a short `differential-fuzz.py --ops commit,rebase,bench --iterations 40` (0 divergences) — NOT the full matrices. Report the acceptance results. Docs + verification only; do NOT touch src/tests/scripts (beyond running them); do NOT commit. |

Ordering: 9a first (the reference is the substantive artifact); 9b after
(it references 9a and closes the programme). One commit per step;
management reviews and commits. 9b is the final commit of the programme.

## Review checklist deltas

* **The reference is accurate to the code, not the plan** — a reviewer
  spot-checks the crate-API references, the invariants (`max_rc < 3`,
  the no-child-increment, the crash-ordering), and the per-op semantics
  against the current tree, not against the plan prose.
* **Current-state, not changelog** — the reference reads as "how it works
  now" in present tense; the phase-by-phase story stays in the plan
  findings (a provenance footnote is fine, narration in the body is not).
* **No duplication** — the op docs link the shared reference rather than
  re-deriving the write path; the reference links (not copies) the
  existing qcow2/ format docs.
* **The follow-ups survive outside the plan** — the overlay-emptying and
  rebase-growth-bound follow-ups + the debug-guard note are in quirks.md
  and/or the reference's future-work section, so they are not lost when
  the plan becomes history.
* **Final acceptance is real but light** — 9b actually runs the
  build/lint/unit/pre-commit gate and the representative smoke (and
  reports numbers); it does not claim green without running, nor does it
  re-run the full matrices unnecessarily.
* **Programme marked complete coherently** — the master plan execution
  table, the retrospective, the defect register, and the plans index all
  agree that all nine phases are done and the defects are resolved.
