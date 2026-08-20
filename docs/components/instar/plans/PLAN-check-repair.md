# `instar check --repair` for QCOW2

## Status: Complete

All 11 phases landed.

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (the QCOW2 refcount / L1 / L2 / snapshot
metadata model, `qemu-img check -r` semantics, the `corrupt`
header bit, KVM/virtio), research as needed to give a confident
answer. Flag any uncertainty explicitly rather than guessing.

The authoritative external references for repair semantics are
the qemu sources — `block/qcow2-refcount.c`
(`qcow2_check_refcounts`, `check_refcounts_l1`,
`check_refcounts_l2`, `rebuild_refcount_structure`,
`qcow2_check_fix_snapshots`) and `block/qcow2.c`
(`qcow2_co_check_locked`, the `BdrvCheckResult` /
`BdrvCheckMode` model) — plus the on-disk layout in
`docs/qcow2/qcow2-refcount.md`, `docs/qcow2/qcow2-l1l2-tables.md`,
and `docs/qcow2/qcow2-format.md`.

All planning documents go into `docs/plans/`. Consult
`ARCHITECTURE.md` for the host VMM / KVM guest / call-table
structure, `AGENTS.md` for build commands and conventions, and
`docs/commentary/` for design rationale.

When we get to detailed planning, each phase gets its own plan
file named `PLAN-check-repair-phase-NN-descriptive.md` in this
directory, tracked via the Execution table below.

I prefer one commit per logical change, and at minimum one
commit per phase. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

`instar check` is feature-complete as a *reporting* tool. The
guest binary (`src/operations/check/src/main.rs`) does a full
QCOW2 walk — header validation, L1/L2 traversal, overlap
detection, refcount validation at all widths (1–64 bit), leak
detection, dirty/corrupt flag handling, extended-L2 subclusters,
and external data files — and reports the findings back through
the `CheckResult` wire struct. That struct already classifies
findings into the granular buckets repair will need
(`src/shared/src/lib.rs`, the "Check operation configuration and
results" block):

- `corruptions` — data-integrity issues (out-of-bounds offsets,
  overlapping metadata).
- `leaks` — allocated-but-unreferenced clusters. The check op
  deliberately treats these as non-fatal: leaks do **not** clear
  `FLAG_VALID`, because the data is intact and the space is
  simply wasted (`check/src/main.rs:262-272`). This already
  mirrors qemu's distinction between "leaks" (exit 3) and
  "errors" (exit 2).
- `refcount_errors` — refcount table/block inconsistencies.
- `chain_errors` — backing-chain validation problems.
- `subcluster_errors` — extended-L2 subcluster bitmap problems.

What is **missing** is any code that *acts* on those findings.
The repair capability is a reserved-but-dead ABI placeholder:

- `CheckConfig::FLAG_REPAIR = 1 << 0` exists with the comment
  "Attempt to repair errors (future feature)", and
  `should_repair()` reads it — but nothing consumes the result.
- `src/vmm/src/main.rs:86` defines `CHECK_CONFIG_FLAG_REPAIR`
  and **never references it again** — there is no `--repair`
  flag on the host `check` CLI surface, so the bit can never
  even be set today.
- There is no repair logic anywhere in `src/operations/` or
  `src/crates/`.

Meanwhile, phases 5–8 of `PLAN-snapshot.md` landed a complete,
well-tested set of pure QCOW2 refcount/L1/L2 mutator primitives
in `src/crates/snapshot/` (`set_refcount_in_block`,
`read_refcount_in_block`, `check_refcount_after_addend`,
`alloc_cluster_in_refblocks`, `for_each_cluster_in_l1`,
`update_snapshot_refcount`, `update_copied_flags_for_l1`, plus
the COPIED-flag rewriters). These are exactly the operations a
refcount repair needs. The mutators are pure functions over
staged byte slices with no I/O, 128 unit tests, and fuzz
coverage — so repair can build on a hardened foundation rather
than re-deriving refcount-width arithmetic.

This plan was promoted from phase 2 of
[PLAN-convert-followups.md](/components/instar/plans/PLAN-convert-followups/). That
umbrella plan tracked two deferred items: the seven `qemu-img`
subcommands (all now shipped, each as its own master plan) and
`check --repair`. Following the precedent that each subcommand
became its own master plan rather than a convert-followups phase,
`check --repair` gets the same treatment here. convert-followups'
phase-2 row is repointed at this plan.

### Scope

**In scope (v1):** QCOW2 only. QCOW2 has by far the richest
metadata to repair and is the only format whose corruption is
both common and mechanically repairable. The repair tiers mirror
`qemu-img check -r`:

- `--repair=leaks` — the **safe tier**. Reclaim
  allocated-but-unreferenced clusters by decrementing their
  refcounts to zero. This is purely additive to free space and
  cannot lose guest-visible data.
- `--repair=all` — the safe tier **plus** the lossy tier:
  rebuild/correct refcount structures, reconcile the
  refcount↔COPIED invariant, and clear the header `corrupt`
  bit once the image validates clean.

**Out of scope (deferred, see Future work):** repair for VMDK /
VHD / VHDX; `qemu-img amend`; snapshot-table repair beyond what
`qcow2_check_fix_snapshots` does for refcounts; refcount-table
*growth* during repair (the snapshot allocator returns
`RefcountExhausted` rather than growing — repair inherits that
limit and reports it rather than guessing).

## Mission and problem statement

After this plan lands:

1. `instar check --repair=leaks <image.qcow2>` reclaims leaked
   clusters in place and reports the count reclaimed, matching
   `qemu-img check -r leaks` exit codes and post-repair state.
2. `instar check --repair=all <image.qcow2>` additionally
   rebuilds inconsistent refcounts, restores the
   refcount↔COPIED invariant, and clears the `corrupt` header
   bit when the result validates clean, matching
   `qemu-img check -r all`.
3. Repair runs **inside the KVM guest**, consistent with every
   other mutating operation (resize / commit / snapshot all
   mutate the live image via the `write_*_sector` call-table
   primitives). No new trust boundary is introduced.
4. A corrupt fixture that `qemu-img check -r` can repair is
   repaired byte-equivalently (or at minimum to a state
   `qemu-img check` declares clean) by instar, verified in
   integration tests and differential fuzzing.
5. The repair mutators reuse `src/crates/snapshot/`'s primitives
   wherever possible; any genuinely new pure logic lands in a
   `repair` module (a `src/crates/check/` planner crate, or a
   `repair` submodule of an existing crate — open question 1).
6. No regression to the existing reporting path: `instar check`
   with no `--repair` flag is byte-identical in output and exit
   code.

## Design overview: the repair safety model

The single most important design decision in this plan is the
safety model, because a buggy repair does not fail loudly — it
silently corrupts an image the user explicitly asked us to fix.
"Force the user to take a backup first" is the blunt framing and
we **reject it**, for three reasons: `qemu-img check -r` does not
do it (parity is instar's whole purpose); mandating a full copy
of a potentially terabyte-scale image to reclaim a few leaked
clusters is absurd UX; and in-place mutation is the established
house style for every instar mutating op. Instead, safety comes
from five concrete properties:

### 1. Tiering: safe vs lossy, mirroring qemu

| Tier | Flag | Operations | Reversibility |
|------|------|------------|---------------|
| Safe | `leaks` | Decrement refcount of allocated-but-unreferenced clusters to 0 | **Lossless** — only frees space provably referenced by nothing |
| Lossy | `all` | Rebuild refcount structures, reconcile COPIED flags, clear `corrupt` bit | **Potentially lossy** — resolves ambiguity; a wrong guess discards a reference |

`leaks` is the only tier we can promise is non-destructive. It is
the conservative default the documentation steers users toward.
`all` is opt-in and carries an explicit warning in `--help` and
docs.

### 2. Dry-run is already the default — and it is free

Plain `instar check` (no `--repair`) is the dry run: it walks the
image and reports every finding without writing a byte. Users
preview exactly what repair would target before opting in. We do
**not** need a separate `--dry-run` flag; the absence of
`--repair` *is* the dry run, matching qemu.

### 3. In-place mutation, no mandatory backup

Repair patches the live file through `write_input_sector` /
`write_output_sector`, exactly as resize/commit/snapshot do. We
do not copy, stage-to-temp-then-rename, or force a `.bak`. The
docs note that `all` is destructive and recommend (not require) a
backup for valuable images — the same posture as `qemu-img`.

### 4. Crash-safe write ordering, guarded by the `corrupt` bit

This is the property that actually protects the user, and the one
a backup cannot provide. If the guest dies mid-repair the image
must not be left *worse*. The discipline applies to the **lossy
`all` tier** (phase 5), whose structural rewrites can leave
mid-flight inconsistency:

- Set the header `corrupt` bit (incompatible feature bit 1)
  **before** the first structural write, so an interrupted
  repair leaves an image that refuses to open read-write until
  re-repaired, rather than one that silently mis-reads.
- Write new/rebuilt refcount blocks **before** repointing the
  refcount table at them; `fsync` between ordering-critical
  phases (the snapshot work added `fsync_input` to the call
  table — repair reuses it).
- Clear the `corrupt` bit **last**, only after a final in-guest
  re-validation pass reports zero corruptions/refcount errors.

The safe **`leaks` tier** (phase 4) deliberately does **not**
touch the `corrupt` bit. Leak reclamation only lowers the
refcounts of clusters the completed whole-image walk proved
unreferenced — monotonic frees that are individually crash-safe,
so a partially-applied leaks repair leaves a consistent (if
still-leaky) image. Setting the `corrupt` bit there would also
*regress* an image that has unrelated, unfixed corruptions:
re-validation would not come back clean, so the bit would be left
set on an image that was openable before. `fsync` ordering still
applies for durability; the `corrupt`-bit guard does not.

### 5. Refuse rather than guess

Where a corruption is ambiguous and qemu itself would bail (e.g.
a refcount-table entry pointing outside the file, an L1 entry
whose L2 cluster overlaps the refcount structures), repair
reports the condition and exits non-zero **without writing**,
rather than fabricating a plausible-but-wrong fix. Repair only
acts where the correct outcome is mechanically determined by the
rest of the metadata. The snapshot allocator's `RefcountExhausted`
path (no refcount-table growth) is one such refuse-don't-guess
boundary inherited directly.

## Open questions

### 1. Where do the repair mutators live? — RESOLVED (phase 1)

**Resolved: a new `src/crates/check/` planner crate**, parallel
to `snapshot` / `commit` / `rebase`, depending on `shared` +
`qcow2` + `snapshot` (to reuse the refcount/L1/L2 primitives).
Rationale mirrors snapshot phase 5's: the `qcow2` crate is
read-mostly and should not gain mutation surface; the existing
`check` *operation* binary is not a library; one crate per
mutating operation is the convention.

The crate name `check` collides with the existing `check`
*operation* package (the lone operation without the `-op`
suffix). **Operator decision this session: adopt the
convention** — rename the operation package `check` → `check-op`
with a `[[bin]] name = "check"` stanza (so the produced binary
stays `check.bin`, exactly like `snapshot-op` → `snapshot.bin`),
freeing the clean `check` name for the planner crate. The
rename's blast radius is contained (only `Makefile`'s
`--exclude check` → `--exclude check-op`); see
[PLAN-check-repair-phase-01-abi.md](/components/instar/plans/PLAN-check-repair-phase-01-abi/).

The reporting-side walk currently in
`src/operations/check/src/main.rs` may later be partially lifted
into this crate so repair and report share one traversal; v1
leaves it in the op and only adds repair planners (phases 2–3).

*Alternative considered:* a `repair` submodule inside
`src/crates/snapshot/`. Rejected — repair is not a snapshot
operation and would muddy that crate's purpose, though it is the
heaviest consumer of snapshot's primitives.

### 2. `--repair` flag surface: `leaks`/`all` enum or bare bool?

Working answer: **`--repair[=leaks|all]`** with `leaks` as the
value when bare, matching `qemu-img check -r` (which takes
`leaks`/`all`). This needs a second ABI flag bit
(`FLAG_REPAIR_ALL`) alongside the existing `FLAG_REPAIR`, since
the wire `CheckConfig` only has one repair bit today. Phase 1
adds it.

### 3. Does repair need a new call-table primitive?

Working answer: **no**. Repair writes via the existing
`write_input_sector` (the input image is the repair target) and
orders via `fsync_input` — both already in the call table from
prior work. This mirrors snapshot's mutating modes, which added
no primitive in phase 5+. Confirm during phase 1 that
`write_input_sector` + `fsync_input` cover every repair write;
if a read-modify-write at sub-sector granularity needs a bounce
buffer, reuse the resize/snapshot bounce pattern rather than a
new primitive.

### 4. Refcount repair: in-place correction or full rebuild?

Working answer: **both, tiered**. For `leaks` and isolated
single-cluster refcount mismatches, correct in place (the
snapshot `set_refcount_in_block` primitive). For an image whose
refcount structure is broadly inconsistent, qemu's
`rebuild_refcount_structure` recomputes the entire refcount table
from the L1/L2 walk and writes a fresh structure. v1 working
answer: implement in-place correction for `leaks` and bounded
mismatches; implement full rebuild for `all` only if the
in-place path cannot converge. Phase 3 (the lossy tier) settles
how far to go; a reasonable v1 floor is "match qemu on the
adversarial fixtures we test."

### 5. How is success measured against qemu-img?

Working answer: **post-repair `qemu-img check` cleanliness**,
not byte-identity of the repaired image. qemu's repair makes
allocation choices (which cluster to claim) that instar need not
reproduce bit-for-bit. The integration and differential tests
assert that after `instar check --repair`, `qemu-img check`
reports the image clean and `qemu-img info` / `qemu-img compare`
agree on guest-visible data. Byte-identity is a non-goal.

### 6. Exit-code semantics

Working answer: **match `qemu-img check -r`**. After a
successful repair, qemu re-checks and returns 0 if clean, 3 if
only leaks remain, 2 if corruptions remain. instar's VMM already
maps `CheckResult` counters to exit codes for the report path;
repair extends that mapping to "counters *after* repair". Phase 6
(host CLI polish) owns the full 0/2/3 mapping; phase 4's minimal
host enablement keeps the existing pass/fail exit behaviour.

## Execution

**Status: COMPLETE.** All 11 phases landed (2026-06-14), plus the
post-plan guest→host repaired-counter wire follow-up that phase 6
deferred (the `repaired_leaks`/`repaired_refcounts`/`repaired_corruptions`
counters now travel on the `CheckResultMessage` protobuf and render in
both the human and JSON `check --repair` output). `instar check
--repair[=leaks|all]` for QCOW2 is shipped, tested, fuzzed, and
documented.

| Phase | Plan | Status |
|-------|------|--------|
| 1. ABI + crate scaffolding: add `FLAG_REPAIR_ALL` to `CheckConfig`, repair-result counters (`repaired_leaks`/`refcounts`/`corruptions`) + `FLAG_REPAIR_INCOMPLETE` to `CheckResult`, create the `src/crates/check/` planner crate (deps `shared`+`qcow2`+`snapshot`; `RepairTier`/`RepairError`/`RepairCounters` surface), and rename the op package `check` → `check-op` (binary stays `check.bin`) to free the crate name. Writable-input-device open deferred to phase 4/5 (open question 3) | PLAN-check-repair-phase-01-abi.md | **Complete** |
| 2. Leak-reclamation planner (safe tier): `reclaim_leaks_in_refblock` — pure per-refblock driver that zeroes the refcount of `rc > 0 && !is_referenced` entries (the guest supplies the per-block referenced predicate), reusing `set_refcount_in_block`; never lowers a live cluster (that is phase 3's over-count case, since the detector's bitmap is boolean); unit tests over synthetic refblocks incl. sub-byte neighbour preservation | PLAN-check-repair-phase-02-leak-planner.md | **Complete** |
| 3. Refcount-rebuild + COPIED reconciliation planner (lossy tier): `account_reference_in_map` (count references into a staged computed-refcount map, overflow→`AmbiguousCorruption`), `correct_refcounts_in_refblock` (both-directions correction to the computed value — raise too-low, lower too-high, free zero-count — generalising phase 2), and `reconcile_copied_flags_for_l1` (wrapper over `snapshot::update_copied_flags_for_l1`). Recounts because the detector's bitmap is boolean; refuses on overflow / over-capacity (refcount-structure growth deferred, OQ7). Pure; unwired | PLAN-check-repair-phase-03-refcount-planner.md | **Complete** |
| 4. Guest wiring — **safe `leaks` tier, end-to-end**: `check-op` depends on the `check` crate; after the detection walk (which builds the reference bitmap), a `repair_leaks_qcow2` pass stages each refblock and calls `reclaim_leaks_in_refblock`, writes back via `write_input_sector`/`fsync_input`, updates the post-repair `CheckResult` (`repaired_leaks`, recomputed `leaks`). Plus the **minimal host enablement** pulled forward: a `--repair` flag on `CheckArgs`, conditional read-write device open in `run_check`, `FLAG_REPAIR` plumbed. No `corrupt`-bit dance — leak reclamation is crash-safe (monotonic frees of unreferenced clusters). Memory-light (one refblock at a time) | PLAN-check-repair-phase-04-guest-leaks.md | **Complete** |
| 5. Guest wiring — **lossy `all` tier** (snapshot-free / uncompressed scope): for the supported scope every valid refcount is 0 or 1, so the recount reuses the detection bitmap (`computed = bmp.test(cidx) ? 1 : 0`) — no counting walk or computed-map memory; `correct_refcounts_in_refblock` corrects both directions and `reconcile_copied_flags_for_l1` re-sets COPIED, under the **crash-safe `corrupt`-bit ordering** (set → correct → reconcile COPIED → clear) this tier needs. Refuses snapshots / compression / external-data / structural corruption with `FLAG_REPAIR_INCOMPLETE`. `--repair[=leaks\|all]` value-parsing + `FLAG_REPAIR_ALL` pulled forward. `account_reference_in_map` + the snapshot/compression-aware counting walk are deferred future work | PLAN-check-repair-phase-05-guest-all.md | **Complete** |
| 6. Host CLI polish: qemu-parity exit codes (0 clean / 3 leaks / 2 errors) mapped from the post-repair `CheckResult` (the `--repair[=leaks\|all]` clap surface already landed in phase 5); repair-result rendering (human + JSON: `repaired_leaks`/`repaired_refcounts`/incomplete); a destructive-`--repair=all` `--help`+stderr warning; and a `--repair`+`--chain` reject. Host-only (`check.bin` unchanged). NOTE: at phase 6 the `repaired_*` counters were not on the guest→host protobuf, so per-counter "Repaired N" rendering was deferred to a guest+proto follow-up; only `FLAG_REPAIR_INCOMPLETE` rendered. **That follow-up has since landed** (the counters are on the `CheckResultMessage` protobuf and render in human + JSON). Exit codes (incl. `not_supported`→63) match qemu | PLAN-check-repair-phase-06-host.md | **Complete** |
| 7. Corrupt fixtures + cross-version baselines (cross-repo: instar-testdata + manifest): extend `custom/check-validation/create-corrupt-images.py` with repair fixtures (refcount-too-high, stale-COPIED, corrupt-bit-set, snapshot-leak, compressed-leak; reuse leaked-cluster/refcount-zero/overlapping), each verified by `qemu-img check` (condition) and `qemu-img check -r all` (cleans the repairable ones); capture cross-version `check` detection baselines (expect ~1-2 profiles — detection is stable); register all in `tests/manifest.json` with repair-tier tags + sha256. Two commits (instar-testdata main + instar branch). No instar source change. End-to-end-validated: all four repairable fixtures repair to qemu-img-clean; refuse fixtures untouched. 80-version baseline capture deferred (detection stable, low value) | PLAN-check-repair-phase-07-baselines.md | **Landed** (instar-testdata pushed: `d491f3f9f`) |
| 8. Integration tests (`tests/test_check_repair.py`): codify the phase-7-verified behaviour — leaks/all tiers repair the fixtures to `qemu-img check`-clean with guest data preserved (`qemu-io read -P`); refuse paths (corrupt-bit / snapshot / compression) stay byte-identical; overlapping is a safe partial repair (leak reclaimed, overlap remains, not worse, exit 2); plus CLI (`--repair`+`--chain` reject, qcow2-only, idempotence). Adds an additive `repair=` param to `run_instar_check`; host-test-only (`check.bin` unchanged) | PLAN-check-repair-phase-08-integration.md | **Complete** |
| 9. Coverage-guided fuzzing of the repair planners (`fuzz_check_repair`): corrupt refblock/L1/L2 buffers in, assert no panic and no out-of-bounds write | PLAN-check-repair-phase-09-fuzz-coverage.md | **Complete** |
| 10. Differential fuzzing: random corruptions injected into a valid image, repaired by both instar and qemu-img, results compared for `qemu-img check` cleanliness and guest-data equivalence | PLAN-check-repair-phase-10-fuzz-differential.md | **Complete** |
| 11. Docs, CHANGELOG, follow-ups: `docs/qcow2/qcow2-refcount.md` repair section, `docs/usage.md` + `--help`, `ARCHITECTURE.md`/`README.md`/`AGENTS.md`, strike through convert-followups phase 2 | PLAN-check-repair-phase-11-docs.md | **Complete** |

Phase plans are written one at a time, at the effort level the
phase warrants, as each is scheduled — matching how the snapshot
family was rolled out. Phases 1, 3, 4, and 5 are high-effort opus
(ABI/safety-ordering judgment, repair correctness — phase 5 is
the riskiest guest phase); phases 2, 6, 9 are medium; phases 7,
8, 10, 11 follow established fixture/test/doc patterns. Phase 4
was split from the original single "guest wiring" phase: the
safe `leaks` tier (here) is end-to-end testable on its own, and
the lossy `all` tier counting-walk (phase 5) is a phase-3-sized
chunk that earns standalone focus.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making. The workflow per step:
plan at high effort → spawn a sub-agent with the brief →
review the actual changed files (not the summary) → fix/retry or
commit. Use `isolation: "worktree"` for the structural-repair
phases (3, 4) where a wrong write is costly; safer phases can
work in the main tree.

### Planning effort

This master plan is high-effort. Per-phase effort is noted in the
Execution table. The refcount-rebuild phase (3), the crash-safe
write-ordering guest phase (4), and the ABI phase (1) are the
high-stakes ones — refcount/metadata repair is subtle and easy to
corrupt further, which is precisely the failure mode the safety
model exists to prevent.

### Management session review checklist

After each step:

- [ ] The intended files changed; no unrelated files touched.
- [ ] `make instar` builds, `make lint` clean.
- [ ] Guest binaries pass `make check-binary-sizes` (384KB cap;
      the check binary grows — watch its budget).
- [ ] `make test-rust` and the relevant `make test-integration`
      pass.
- [ ] `pre-commit run --all-files` passes.
- [ ] **Safety-model invariants hold:** the dry-run pass never
      writes; `leaks` tier never touches a referenced cluster;
      the `corrupt` bit is set before the first structural write
      and cleared only after a clean re-validation; refuse-
      don't-guess paths exit non-zero without writing.
- [ ] Repair planners reuse `src/crates/snapshot/` primitives
      rather than re-deriving refcount-width arithmetic.
- [ ] No `unsafe` beyond what the existing crates require; the
      planner crate is safe Rust top-to-bottom.

## Administration and logistics

### Success criteria

* `make instar` builds and `make lint` is clean.
* Guest binaries pass `make check-binary-sizes` (384KB limit).
* All Rust unit tests pass (`make test-rust`).
* All Python integration tests pass (`make test-integration`),
  including the new `tests/test_check_repair.py`.
* `pre-commit run --all-files` passes.
* Repair logic lives in a `no_std`-compatible shared crate under
  `src/crates/`, reusing `src/crates/snapshot/` primitives.
* `instar check --repair=leaks` and `=all` produce images that
  `qemu-img check` declares clean across the corrupt-fixture
  matrix; differential fuzzing finds no divergence in
  cleanliness or guest-visible data.
* The reporting-only `instar check` path is byte-identical.
* Docs (`docs/qcow2/qcow2-refcount.md`, `docs/usage.md`),
  `--help`, `ARCHITECTURE.md`, `README.md`, `AGENTS.md`, and
  `CHANGELOG.md` are updated; convert-followups phase 2 is
  struck through.

### Future work

* **Repair for VMDK / VHD / VHDX.** QCOW2 first because its
  metadata is the richest and most mechanically repairable.
* **Refcount-table growth during repair.** Inherited limit from
  the snapshot allocator's `RefcountExhausted` boundary; repair
  reports rather than grows. Lift resize's growth helper if a
  real workflow demands it.
* **`qemu-img amend`** as a sibling capability (changing image
  options post-creation).
* **Snapshot-table structural repair** beyond refcount fixes.
* **Snapshot- and compression-aware recount** (the lossy `all`
  tier's deferred extension). v1 refuses snapshotted, compressed,
  and external-data images because the `bmp`-as-count identity
  (every correct refcount is 0 or 1) only holds for the
  snapshot-free, uncompressed, single-file scope; a true recount
  via `account_reference_in_map` over the snapshot L1/L2s and the
  compressed cluster host ranges would lift those refusals.
  (Deferred from phase 5/7; `account_reference_in_map` is shipped
  but unused in v1.)
* **80-version `check` detection baseline capture for the repair
  fixtures.** Phase 7 registered the fixtures and verified
  detection on the host qemu-img but did not run the full
  80-qemu-version baseline sweep (detection output is stable, so
  low value); capture it if a regression ever suggests
  version-dependent detection drift on these images.

### Bugs fixed during this work

* **All-tier L2 staging over-capacity guard (pre-push audit).**
  `repair_all_qcow2`'s L2 staging loop guarded only on the entry
  count (`staged_count >= REPAIR_ALL_MAX_STAGED_L2`), dropping the
  byte-extent half of snapshot's `stage_l2_set` guard (`cursor +
  cluster_size > cap_end`). An image with `cluster_bits >= 14` and
  more active L2 tables than fit in the 2 MiB staging arena would
  write past the arena into the adjacent guest scratch buffers
  (sandbox-contained — the VMM still clamps every write-back to the
  device capacity — but a real guest-side OOB write that could also
  produce wrong on-disk metadata). Fixed by restoring the
  byte-extent bound; the fuzz target's `cluster_bits <= 14` cap is
  why it was not caught earlier.
* **CI excluded the wrong `check` crate (pre-push audit).**
  `.github/workflows/functional-tests.yml`'s `cargo test
  --workspace` excluded `check` (the planner crate, whose unit
  tests are this work's primary Rust test surface) instead of
  `check-op` (the no_main guest binary), after the phase-1 package
  rename. The Makefile was updated but the workflow was not. Fixed
  to `--exclude check-op`.
* **Fixed-VHD resize dropped the footer (surfaced by CI).** A fixed
  VHD has raw data at the head and its footer only in the last 512
  bytes, so `resize`'s header-only format detection (both the host
  `probe_resize_target` and the guest's `detect_format_from_header`)
  misdetected it as raw and routed it through the raw resize path,
  which `set_len`s to the new size and drops the footer — leaving a
  footerless raw file. Pre-existing on develop, but masked there
  because plain `check` returned 0 for the degraded format; this
  work's phase-6 `not_supported`→63 exit-code parity (which matches
  `qemu-img check`, verified) made `check` return 63 on the footerless
  result, failing `test_resize`'s `vhd_1M_to_4M_fixed` cases. Fixed by
  probing the tail for a VHD footer in both detection sites when the
  header detection returns raw (mirroring the `info` op's
  `detect_vhd_footer` tail check), so a fixed VHD resizes as a VHD and
  keeps its footer.

### Documentation index maintenance

* `index.md` — add a *Master plans* row (date 2026-06-13, link,
  intent, status "Drafted, not started", phase links as written).
* `order.yml` — add `PLAN-check-repair.md` after
  `PLAN-snapshot.md`. Phase files are not added to `order.yml`.
* `PLAN-convert-followups.md` — repoint the phase-2 row at this
  master plan.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
