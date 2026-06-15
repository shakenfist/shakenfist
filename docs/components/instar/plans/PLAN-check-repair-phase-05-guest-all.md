# PLAN-check-repair phase 05: guest wiring — lossy `all` tier

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly and ground your
answers in what the code actually does today; do not speculate
when you could read. Research qemu's `qcow2_check_refcounts` /
`rebuild_refcount_structure` and the COPIED invariant
(`docs/qcow2/qcow2-refcount.md`) where useful. The code this
phase touches or composes:

- `src/operations/check/src/main.rs` — `check_qcow2` and the
  phase-4 leaks-tier wiring: the detection `bmp`, the
  `repair_leaks_qcow2` pass, the snapshot guard
  (`hdr.nb_snapshots > 0` → refuse), the scratch-buffer
  const layout (top-anchored, clear of the base-anchored `bmp`,
  with a runtime `bmp`-extent guard), and the
  `write_input_byte_range` / `read_input_byte_range` helpers.
- `src/crates/check/src/qcow2.rs` — phase-3's pure primitives:
  `correct_refcounts_in_refblock(refblock, entries_in_block,
  refcount_bits, computed_for)` and
  `reconcile_copied_flags_for_l1(l1_bytes, cluster_bits,
  l2_for_index, refcount_for_cluster, extended_l2)`. (Phase-3's
  `account_reference_in_map` is **not** used in this phase — see
  the Situation note.)
- `src/operations/snapshot/src/main.rs` — the L1/L2 **staging**
  pattern this phase mirrors for COPIED reconciliation
  (`L2_STAGING` region, `MAX_STAGED_L2`, the per-L2 read into a
  bounded buffer) and the header-field write-back via
  `write_input_byte_range`.
- `src/crates/qcow2/src/lib.rs` — `INCOMPATIBLE_FEATURES_OFFSET
  = 72`, `INCOMPAT_CORRUPT = 1 << 1`, `INCOMPAT_COMPRESSION`,
  `INCOMPAT_EXTERNAL_DATA`, `INCOMPAT_EXTENDED_L2`, and the
  `QcowHeader` fields (`nb_snapshots`, `incompatible_features`,
  `corrupt`, `cluster_bits`, etc.).
- `src/vmm/src/main.rs` — `CheckArgs` `--repair` (a bool today)
  and the `check_flags` assembly; the `CHECK_CONFIG_FLAG_REPAIR`
  /`..._REPAIR_ALL` plumbing.

The parent master plan is
[PLAN-check-repair.md](/components/instar/plans/PLAN-check-repair/). Read its "Design
overview" — **safety point 4 (crash-safe `corrupt`-bit ordering)
applies to THIS tier** — and open questions 4, 5, 7. This is
phase 5 of eleven: **the riskiest guest phase.** A wrong refcount
or COPIED flag silently corrupts an image the user asked us to
fix.

I prefer one commit per logical change, and at minimum one commit
per phase. The commit must build, pass tests, and have a clear
message.

## Situation

Phase 4 shipped the safe `leaks` tier (free `bmp`-false clusters,
no `corrupt`-bit action) and refuses snapshotted images. This
phase adds the lossy `all` tier: correcting *wrong* refcounts in
both directions and re-establishing the refcount↔COPIED
invariant, under the crash-safe `corrupt`-bit ordering.

### The key simplification: for the supported scope, the recount is the `bmp`

The master plan and phase 3 framed the `all` tier as a
whole-metadata counting walk into a computed-refcount map
(`account_reference_in_map`). Grounding phase 5 surfaced a much
simpler, safer truth for the **scope this phase supports**:

> In a valid **snapshot-free, uncompressed, single-file** qcow2,
> every cluster's correct refcount is exactly **0 or 1**. Every
> metadata cluster (header, L1, refcount table, refblocks, L2s)
> is referenced once; every allocated data cluster is referenced
> by exactly one L2 entry; free clusters are 0.

So the "computed" refcount is simply `bmp.test(cidx) ? 1 : 0` —
the **existing detection bitmap**, reused. No separate counting
walk, no computed-map memory, no `account_reference_in_map`.
`correct_refcounts_in_refblock` with a `bmp`-backed `computed_for`
closure corrects every refcount in both directions (raise
too-low, lower too-high, free zero-count), and
`reconcile_copied_flags_for_l1` with the same closure sets COPIED
on every allocated entry (all refcounts are 1).

Refcount > 1 only arises from **sharing** — internal snapshots,
or packed compressed clusters sharing a host cluster. Those are
exactly the cases this phase **refuses** (below), so the
`bmp`-as-count identity holds for everything it repairs.
`account_reference_in_map` + a real computed map (phase 3, built
and tested) is the deferred extension that lifts the restriction
by actually counting shared references — **future work**, needing
the snapshot-table + snapshot-L1/L2 walk this phase also defers.

### Scope and the refuse-don't-guess guards

The `all`-tier correction runs only when the `bmp`-as-count
identity is valid and the image is otherwise structurally sound.
The guest refuses (sets `FLAG_REPAIR_INCOMPLETE`, does not run
the correction) when any of:

- `hdr.nb_snapshots > 0` — `bmp` omits snapshot references (the
  phase-4 guard; here it also blocks the all tier). Refusing
  *all* repair on snapshotted images is already done in phase 4.
- `INCOMPAT_COMPRESSION` set — packed compressed clusters can
  legitimately share a host cluster (refcount > 1), so
  `bmp`-as-1 would wrongly lower them. (The leaks tier is still
  safe here — it only frees `bmp`-false clusters.)
- `INCOMPAT_EXTERNAL_DATA` set — data clusters live in a separate
  file; the refcount model differs. Conservatively refuse v1.
- `result.corruptions > 0` — **structural** damage the all tier
  cannot fix (cluster overlaps, out-of-bounds offsets, bad
  header). Refuse rather than write refcounts onto a
  structurally-broken image, and do **not** set the `corrupt`
  bit (no regression). Only when `corruptions == 0` are the
  remaining issues (refcount mismatches, leaks, stale COPIED)
  fully fixable, so the post-correction image is clean **by
  construction** — no second validation walk needed.

`INCOMPAT_EXTENDED_L2` is **supported** (subclusters don't create
refcount > 1; `reconcile_copied_flags_for_l1` handles 16-byte
entries — pass `extended_l2` from the header).

### Crash-safe `corrupt`-bit ordering (why this tier needs it)

Unlike leak reclamation, the all-tier correction is **not**
individually crash-safe: raising a too-low refcount and then
crashing before COPIED is reconciled leaves COPIED inconsistent
with the refcount (qemu would flag it). So this tier follows the
master-plan safety discipline:

1. Set `INCOMPAT_CORRUPT` (offset 72, bit 1) in the header; write
   the header bytes (`write_input_byte_range`); `fsync_input(0)`.
2. Correction pass: per refblock, stage → `correct_refcounts_in_refblock`
   (bmp closure) → write back if changed. `fsync_input(0)` after
   the pass.
3. COPIED reconciliation pass: stage the active L1 + its L2s;
   `reconcile_copied_flags_for_l1` (bmp closure); write back the
   changed L1 + L2 clusters. `fsync_input(0)`.
4. Clear `INCOMPAT_CORRUPT`; write the header bytes;
   `fsync_input(0)`.

An interrupted run leaves the `corrupt` bit set, so the image
refuses read-write open until re-repaired — never silently
mis-read. If any write in steps 2–3 fails, abort with the
`corrupt` bit left set and `FLAG_REPAIR_INCOMPLETE`.

### Dispatch (combining with phase 4)

- `nb_snapshots > 0` → refuse all repair, `INCOMPLETE` (phase 4).
- else if `repair_all` and supported (no compression, no external
  data, `corruptions == 0`) → run the **all-tier pass** (steps
  1–4 above); it subsumes leak freeing (zero-count entries are
  lowered to 0), so the separate leaks pass is **not** also run.
- else if `repair_all` and unsupported → run the phase-4 leaks
  pass (safe) and set `FLAG_REPAIR_INCOMPLETE` (all-tier refused).
- else (`repair`, leaks only) → phase-4 leaks pass.

### What this phase produces

1. **Guest**: an `all`-tier repair pass in
   `src/operations/check/src/main.rs` implementing the dispatch +
   steps 1–4, reusing phase-3's `correct_refcounts_in_refblock`
   and `reconcile_copied_flags_for_l1` with `bmp`-backed closures,
   the phase-4 write-back helpers, and a snapshot-style bounded
   L1/L2 staging region. Post-repair `CheckResult` reflects the
   corrections (`repaired_refcounts` += raised+lowered,
   `repaired_leaks` += freed, recomputed `leaks`/`corruptions`,
   `FLAG_REPAIR_INCOMPLETE` where refused/aborted). New scratch
   buffers extend the phase-4 top-anchored layout with
   compile-time no-overlap asserts and the runtime `bmp`-extent
   guard; over-capacity → `INCOMPLETE`.
2. **Host**: `CheckArgs.repair` becomes `--repair[=leaks|all]`
   (value `leaks` when bare); `all` sets `FLAG_REPAIR_ALL` in
   addition to `FLAG_REPAIR`. (The `--help` warning text and the
   0/2/3 exit-code mapping remain phase 6.)

### What this phase does NOT do (deferred)

- Snapshot-aware and compression-aware recount (the real
  `account_reference_in_map` counting walk over snapshot
  L1/L2 + packed-compressed sharing) — future work.
- Refcount-table growth / relocation — `INCOMPLETE` (OQ7).
- External-data-file repair — `INCOMPLETE` v1.
- `--help` warning text, 0/2/3 exit codes — phase 6.
- Corrupt-fixture round-trip integration — phase 8. This phase's
  verification is the gates plus a **healthy-image `--repair=all`
  no-op smoke** (clean image stays byte-identical and `qemu-img
  check`-clean) and, if a controlled refcount-mismatch fixture is
  cheaply constructible, a single round-trip; otherwise the
  fixture round-trip waits for phase 8 tooling.

## Open questions

### 1. Use the `bmp` shortcut, or `account_reference_in_map` + a computed map?

**Resolved: the `bmp` shortcut, for the supported scope.** It is
provably exact for snapshot-free/uncompressed images (all
refcounts 0/1), needs no extra walk or memory, and reuses the
already-built `bmp`. The counting-map machinery (phase 3) is
reserved for the snapshot/compression extension. Documented so a
future phase wiring `account_reference_in_map` knows the
restriction it lifts.

### 2. Do we need a second validation walk before clearing the `corrupt` bit?

**Resolved: no.** The all-tier pass runs only when `corruptions
== 0`, so the sole pre-existing issues are refcount/leak/COPIED —
all fully corrected to the `bmp`-derived ground truth. The
post-correction image is clean by construction, so the `corrupt`
bit is cleared in step 4 without a re-walk. A write failure mid
steps 2–3 leaves the bit set + `INCOMPLETE`.

### 3. Active L1 only, or snapshot L1s too, for COPIED?

**Resolved: active L1 only.** Snapshotted images are refused
entirely, so only the active L1 exists to reconcile. (When the
snapshot extension lands, COPIED on the active L1 stays keyed on
refcount == 1; snapshot L1s keep COPIED clear because their
clusters are shared.)

### 4. Over-capacity (image too large to stage L1/L2/refblocks)?

**Resolved: refuse the all tier (`INCOMPLETE`), never partial.**
The bounded L1/L2 staging (snapshot's `MAX_STAGED_L2` pattern)
and the `bmp`-extent runtime guard bound the repairable image;
beyond them the guest sets `FLAG_REPAIR_INCOMPLETE` and does not
write. (The leaks tier, which streams one refblock at a time,
has a higher ceiling and still applies.)

### 5. Minimal host `--repair=all`, or full phase-6 surface?

**Resolved: pull the value-parsing forward (`--repair[=leaks|
all]`), leave the polish to phase 6.** You cannot exercise the
all tier without a way to set `FLAG_REPAIR_ALL`; the clap
optional-value flag is the smallest thing that does it. Phase 6
adds the `all`-is-destructive `--help` warning and the 0/2/3
exit-code mapping.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | high | opus | worktree | Host `--repair[=leaks|all]` in `src/vmm/src/main.rs`. Change `CheckArgs.repair` from `bool` to an optional value: `#[arg(long, value_name = "MODE", num_args = 0..=1, default_missing_value = "leaks")] repair: Option<RepairMode>` where `RepairMode { Leaks, All }` is a clap `ValueEnum` (bare `--repair` ⇒ `Leaks`). In the `check_flags` assembly, set `CHECK_CONFIG_FLAG_REPAIR` when `repair.is_some()` and additionally `CHECK_CONFIG_FLAG_REPAIR_ALL` when `Some(All)`. Update the conditional device open to key off `args.repair.is_some()` (was `args.repair`). Add the `CHECK_CONFIG_FLAG_REPAIR_ALL` mirror constant near line 86 (the shared `FLAG_REPAIR_ALL` is `1 << 4`) with `#[allow(dead_code)]` if needed to match the existing mirror style. Leave the `--help` warning text and exit-code mapping for phase 6 (a short doc comment is fine). Build `cargo build -p vmm`. Opus: the clap optional-value semantics and keeping the read-only path untouched (only `is_some()` should trip the RW open) are the traps. |
| 5b | high | opus | worktree | The guest all-tier pass in `src/operations/check/src/main.rs`. (i) Scope guard: compute `all_supported = repair_all && hdr.nb_snapshots == 0 && (hdr.incompatible_features & (INCOMPAT_COMPRESSION | INCOMPAT_EXTERNAL_DATA)) == 0 && result.corruptions == 0`. Implement the dispatch in the Situation section: snapshots → refuse (already handled by the phase-4 guard); `repair_all && all_supported` → run the all-tier pass (do NOT also run `repair_leaks_qcow2`); `repair_all && !all_supported` → run `repair_leaks_qcow2` + set `FLAG_REPAIR_INCOMPLETE`; plain `repair` → `repair_leaks_qcow2`. (ii) Add `unsafe fn repair_all_qcow2(...)` performing steps 1–4: set `INCOMPAT_CORRUPT` (read the 8 header bytes at offset 72, OR in `INCOMPAT_CORRUPT`, `write_input_byte_range(.., 72, &patch)`, `fsync_input(0)`); the refcount-correction pass (iterate the refcount table exactly as `repair_leaks_qcow2` does, stage each whole refblock, call `check::qcow2::correct_refcounts_in_refblock(refblock, entries_per_block, refcount_bits, |local_idx| Some(if bmp.test(rt_idx*entries_per_block+local_idx) {1} else {0}))`, write back if the returned tally is non-zero, accumulate raised/lowered/freed), `fsync_input(0)`; the COPIED pass (stage the active L1 from `l1_table_offset`/`l1_size` and its L2s into a bounded staging region mirroring snapshot's `L2_STAGING`/`MAX_STAGED_L2`, call `check::qcow2::reconcile_copied_flags_for_l1(l1, cluster_bits, |l1_idx| staged L2 mut slice, |cidx| Some(if bmp.test(cidx) {1} else {0}), extended_l2)`, write back changed L1 + L2 clusters), `fsync_input(0)`; clear `INCOMPAT_CORRUPT` and write+fsync the header. Return a `(raised, lowered, freed, rewritten_copied)` tally. On any write error or over-capacity (L2 count > the staging bound, or the `bmp`-extent guard would be violated), ABORT: leave the `corrupt` bit set, return what happened, and the caller sets `FLAG_REPAIR_INCOMPLETE`; never panic/unwrap. (iii) Add the scratch buffers (active-L1 buffer, L2 staging region) extending the phase-4 top-anchored layout, with compile-time no-overlap asserts and the runtime `bmp`-extent check. (iv) Update `result`: `repaired_refcounts += raised + lowered`, `repaired_leaks += freed`, decrement `leaks`/`total_errors` by `freed`, clear `FLAG_HAS_LEAKS` if `leaks == 0`; set `FLAG_REPAIR_INCOMPLETE` on the unsupported/aborted paths. Opus: this is the highest-stakes write path — the corrupt-bit ordering, the bmp-closure cluster-index mapping (must match the detector and phase 4), the bounded L2 staging, and the abort-leaves-corrupt-bit semantics are all load-bearing. |
| 5c | medium | sonnet | worktree | Verify and commit. From the worktree `src/` (cargo target dir redirected to an owned path): `cargo build -p vmm -p check-op`, `make test-rust`, `make instar`, `make check-binary-sizes` (report `check.bin`'s new size; must stay < 384 KiB), `make lint`, `pre-commit run --all-files` (watch the rustfmt comment-alignment trap). Then smokes with the freshly-built `instar`: (a) **healthy `--repair=all` no-op** — `qemu-img create -f qcow2 /tmp/clean.qcow2 64M`, write data via `qemu-io`, `instar check --repair=all /tmp/clean.qcow2`, assert exit success, image **sha256-unchanged** (a clean image needs no correction, so the corrupt-bit is set-then-cleared but refcounts/COPIED are already correct → ideally no net content change; if the set/clear of the corrupt bit alters bytes, confirm `qemu-img check` is still clean and the only delta is the header incompat field returning to its original value — i.e. net sha256 SHOULD match); (b) **snapshotted image refused** — reuse a diverged-snapshot fixture, `instar check --repair=all`, assert image sha256-unchanged and `qemu-img check` clean + snapshot intact (the phase-4 guard covers it); (c) **compressed image refused for all-tier** — `qemu-img create` + `qemu-img convert -c` to a compressed qcow2, `--repair=all`, assert no corruption (`qemu-img check` clean). If `qemu-io`/compressed creation is unavailable, say so and fall back to the healthy + snapshot smokes. Stage and present ONE commit (5a+5b). The message explains: the lossy all tier corrects refcounts in both directions and reconciles COPIED for snapshot-free/uncompressed images by reusing the detection bitmap as the ground-truth count (refcount == bmp ? 1 : 0), under the crash-safe corrupt-bit ordering; it refuses snapshots/compression/external-data/structural-corruption (INCOMPLETE); account_reference_in_map and the snapshot-aware recount are deferred; the --help warning and 0/2/3 exit codes are phase 6. |

## Agent guidance

### Execution model

Sub-agents implement in the `check-repair` worktree (itself the
isolation — no nested worktree); the management session reviews
the actual diff, runs the gates and smokes, and commits. 5a (vmm)
and 5b (check op) touch disjoint files; they land as one commit.

### Model and effort notes

- **5a and 5b are high-effort opus.** 5b is the riskiest write
  path in the whole project: a wrong refcount/COPIED or a botched
  corrupt-bit ordering silently corrupts the image. The value is
  in getting the ordering, the bmp-closure index math, the
  bounded staging, and the abort semantics exactly right.
- **5c is medium sonnet**: scripted verify + the three smokes,
  which must each confirm no corruption.

### Management session review checklist

- [ ] The all-tier pass runs ONLY when `repair_all && nb_snapshots
      == 0 && no compression/external-data && corruptions == 0`.
- [ ] On refused paths nothing structural is written and (where
      applicable) the leaks pass still runs; `FLAG_REPAIR_INCOMPLETE`
      set.
- [ ] `corrupt` bit: set before the first correction write,
      cleared only after COPIED reconciliation succeeds; left set
      on any abort.
- [ ] `correct_refcounts_in_refblock`/`reconcile_copied_flags_for_l1`
      closures use `bmp.test` with the SAME cluster-index math as
      the detector and phase 4.
- [ ] L2 staging is bounded; over-capacity → `INCOMPLETE`, no
      partial write. Scratch asserts + runtime `bmp`-guard hold.
- [ ] No `account_reference_in_map` use (v1 reuses `bmp`); no
      `unsafe`/panic in the planners; `RepairError` handled.
- [ ] `make check-binary-sizes`: `check.bin` < 384 KiB.
- [ ] Healthy `--repair=all` is a net no-op (sha256 unchanged,
      `qemu-img check` clean); snapshot/compressed images refused
      without corruption.
- [ ] `make test-rust`, `make lint`, `pre-commit` clean.

## Administration and logistics

### Success criteria

* `instar check --repair=all` on a snapshot-free, uncompressed
  qcow2 corrects refcount mismatches (both directions), frees
  leaks, and reconciles COPIED, under the crash-safe `corrupt`-bit
  ordering, leaving `qemu-img check` clean.
* Snapshotted / compressed / external-data / structurally-corrupt
  images are refused (`FLAG_REPAIR_INCOMPLETE`) without
  corruption; the leaks tier still applies where safe.
* The read-only `check` path and the phase-4 leaks tier are
  unchanged.
* `make instar`, `make test-rust`, `make check-binary-sizes`,
  `make lint`, `pre-commit` all pass; `check.bin` < 384 KiB.
* Lands in one commit on the `check-repair` branch.

### Future work created by this phase

- **Snapshot- and compression-aware recount** via
  `account_reference_in_map` + the snapshot-table / snapshot-L1
  / packed-compressed walk — lifts the scope restriction.
- **External-data-file** repair.
- **Refcount-structure growth** (OQ7).

### Bugs fixed during this work

- **Compression gate missed zlib (caught by the phase-5 smoke).**
  The first cut gated compression on the `INCOMPAT_COMPRESSION`
  header bit, but that bit only flags **zstd** — standard **zlib**
  compression sets no incompatible-feature bit (compression is
  per-L2-entry via `OFLAG_COMPRESSED`). So a zlib-compressed image
  passed the gate, the all tier ran, and it corrupted the metadata
  (`qemu-img check`: "copied flag must never be set for compressed
  clusters"; and shared compressed host clusters would be wrongly
  lowered to refcount 1). Fix: the detection L2 walk sets a
  `uses_compression` flag on any `OFLAG_COMPRESSED` entry, and the
  `all_supported` gate refuses when it is set (the leaks tier
  stays safe on compressed images — it only frees `bmp`-false
  clusters, which the walk marks correctly). Verified: a fresh
  zlib-compressed image under `--repair=all` is now refused
  (byte-identical, `qemu-img check` clean, data intact). The
  smoke test that caught this ran *before* the phase-5 commit, so
  no corrupting build was committed.

### Documentation index maintenance

This is a phase plan, not a master plan: **not** added to
`order.yml`. The master plan's phase-5 row is updated to "Landed"
once the commit is in (and its description corrected to the
`bmp`-shortcut design).

### Back brief

Before executing any step, back brief the operator on your
understanding — especially the `bmp`-as-count identity and the
scope it is valid for, the refuse-don't-guess guards, and the
crash-safe `corrupt`-bit ordering (set before writes, clear only
after a fully-successful correction + COPIED pass).
