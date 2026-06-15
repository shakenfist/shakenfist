# PLAN-check-repair phase 11: docs, CHANGELOG, follow-ups

## Prompt

Before responding to questions or discussion points in this
document, explore the codebase and **verify every technical claim
against the implemented code** — this is a documentation phase, so
the hazard is writing something plausible that the code does not
actually do. Read the implementation, not just the earlier plans.
The files this phase touches:

- `docs/usage.md` — the `check` subsection (currently documents
  only `--chain`); add `--repair`.
- `docs/qcow2/qcow2-refcount.md` — has `## Consistency Checking`
  and `## Implementation Notes`; add a repair deep-dive section.
- `docs/quirks.md` — the home for documented, intentional
  instar/qemu-img differences; add the `--repair=leaks` scope
  difference phase 10 surfaced.
- `README.md` — the `check` section (~163) and the "22 fuzz
  targets" line (~837).
- `ARCHITECTURE.md` — the fuzz-target count (~872) and prose
  (~513, ~896); a repair architecture note.
- `AGENTS.md` — the `check` one-liner (~63) and the test-file list
  (~273).
- `docs/testing.md` — the fuzz-target table (~551) and the
  `test_check_repair.py` integration suite.
- `CHANGELOG.md` — the `## [Unreleased] / ### Added` block.
- `docs/plans/index.md` — the master-plan status row (~31).
- `docs/plans/PLAN-convert-followups.md` — the phase-2 row (~76).
- `docs/plans/PLAN-check-repair.md` — the phase-11 row → Landed.

Ground truth to read before writing:
- `src/operations/check/src/main.rs` — the guest repair dispatch:
  the `all_supported` gate (`repair_all && nb_snapshots == 0 &&
  !uses_compression && (incompatible_features & ...) == 0 &&
  corruptions == 0`), the crash-safe corrupt-bit ordering (set
  INCOMPAT_CORRUPT + fsync → correct refcounts + fsync → reconcile
  COPIED + fsync → clear bit + fsync), and the snapshot/compression
  refuse paths.
- `src/vmm/src/main.rs` — `CheckArgs.repair` and the already-written
  `--repair[=leaks|all]` clap help (lines ~2970+; it is
  comprehensive — verify it, do not rewrite it), the exit-code
  mapping (0/2/3/63), and the `--repair` + `--chain` rejection.
- `src/crates/check/src/qcow2.rs` — the planner doc comments (the
  source of truth for what each tier does, including "the safe tier
  never lowers a live cluster's refcount").
- The phase 8–10 plans for the behaviours already verified.

The parent master plan is
[PLAN-check-repair.md](/components/instar/plans/PLAN-check-repair/). This is phase 11 of
eleven — the final phase: documentation, CHANGELOG, and the
convert-followups strike-through that close out the master plan.
Phases 1–10 delivered the feature, fixtures, integration suite,
and both fuzzers.

I prefer one commit per logical change; the commit must have a
clear message. This phase is **documentation only** — no source
change, so every binary (`instar`, `check.bin`, …) is
byte-identical.

## Situation

`check --repair` is complete and tested (phases 1–10): the ABI and
planner crate, both repair tiers in the guest, the host CLI, the
corrupt-fixture matrix, `tests/test_check_repair.py`, and the
coverage (`fuzz_check_repair`) and differential (`op_repair`)
fuzzers all landed. What remains is to make the feature
discoverable and to record the design where users and future
maintainers will find it — and to close the convert-followups
phase-2 row that spawned this master plan.

Two facts established during the build feed the docs:

1. **The repair safety model** (settled in the master plan's
   prerequisite work): tiered (`leaks` safe/lossless vs `all`
   lossy), dry-run is *not* the default (a bare `--repair` acts; it
   is the read-only `check` that is the safe default), in-place
   mutation with no mandatory backup, crash-safe `corrupt`-bit
   write ordering for the lossy tier, and refuse-rather-than-guess
   at every ambiguous boundary (snapshots, compression, external
   data, corrupt-bit-set, refcount-table exhaustion, structural
   overlaps).
2. **An intentional instar/qemu-img divergence** surfaced by phase
   10: instar's `--repair=leaks` is *narrower* than `qemu-img check
   -r leaks`. instar's safe tier only frees unreferenced clusters
   and never lowers a referenced cluster's refcount (over-count
   correction is the lossy `all` tier's job), whereas qemu-img's
   `-r leaks` also trims over-counts. So a refcount-too-high cluster
   stays flagged after `instar --repair=leaks` but is cleaned by
   `qemu-img -r leaks`. This is a deliberate conservatism, and
   `docs/quirks.md` is where such documented differences live.

The `--help` text is **already written** (comprehensive clap
doc-comments on `CheckArgs.repair`); this phase verifies it reads
correctly rather than rewriting it. `index.md` and `order.yml`
already carry the master plan (added at draft time); only the
`index.md` *status* needs flipping.

## What each file gets

- **`docs/usage.md`** — a `--repair` bullet under `check`: the two
  tiers, qcow2-only, in-place, `--repair` rejects `--chain`, and a
  pointer to the refcount deep-dive.
- **`docs/qcow2/qcow2-refcount.md`** — a new `## Repairing Refcount
  Inconsistencies` section after `## Consistency Checking`: the
  leaks tier (reclaim unreferenced clusters), the all tier (recount
  against the computed-refcount map: raise under-counts, lower
  over-counts, free zero-counts, reconcile COPIED), the crash-safe
  corrupt-bit ordering, and the refuse-don't-guess boundaries.
  Grounded in the planner doc comments and the guest dispatch.
- **`docs/quirks.md`** — the `--repair=leaks` scope difference (fact
  2 above), framed as intentional, with the rationale (the safe
  tier never lowers a live cluster's refcount) and the workaround
  (`--repair=all` to match qemu-img's leaks-tier over-count
  trimming).
- **`README.md`** — `--repair` in the `check` examples/feature list;
  bump "22 fuzz targets" → "23".
- **`ARCHITECTURE.md`** — a repair note (planner crate `crates/check`
  + guest dispatch + crash-safe ordering, reusing snapshot
  primitives); bump the fuzz count to 23 and name `fuzz_check_repair`
  in the prose.
- **`AGENTS.md`** — extend the `check` one-liner to mention repair;
  add `tests/test_check_repair.py` to the test-file list.
- **`docs/testing.md`** — a `fuzz_check_repair` row in the
  fuzz-target table (Buffer-based, check), a sentence on what it
  covers, and the `test_check_repair.py` suite + the `op_repair`
  differential operation.
- **`CHANGELOG.md`** — an `## [Unreleased] / ### Added` entry for
  `instar check --repair` (PLAN-check-repair phases 1–11): the two
  tiers, qcow2-only, the safety model, and the test/fuzz coverage.
- **`docs/plans/index.md`** — status row → Complete (all 11 phases
  landed).
- **`PLAN-convert-followups.md`** — strike through the phase-2 row
  (`~~check --repair~~`), marked Complete with a pointer here.
- **`PLAN-check-repair.md`** — phase-11 row → Landed.

## Open questions

### 1. One docs commit, or split?

**Resolved: one commit.** The whole phase is a single logical
change (document a shipped feature) with no code risk; one
well-described commit keeps the history legible. The message
enumerates the files and the two design facts (safety model +
leaks-tier quirk).

### 2. Rewrite `--help`?

**Resolved: no — verify only.** The clap doc-comments on
`CheckArgs.repair` already cover both tiers, the lossy warning, and
the qcow2-only restriction. Read them to confirm they are accurate
and well-phrased; change only if something is wrong, and if so note
that this is the one source touch (and re-confirm `check.bin` is
still byte-identical — a comment-only change to `main.rs` rebuilds
the host binary but not the guest `check.bin`).

### 3. Does `docs/qcow2/qcow2-refcount.md` or a new `docs/check.md`
host the deep-dive?

**Resolved: extend `qcow2-refcount.md`.** Repair is fundamentally
about refcount/COPIED reconciliation; the refcount doc already has
the `## Consistency Checking` context the repair section builds on,
and there is no standalone `docs/check.md` (check is documented in
`usage.md` + `README.md`). A new top-level doc would orphan the
context.

### 4. Where does the leaks-tier divergence go — quirks or the
refcount doc?

**Resolved: `docs/quirks.md`** (the canonical home for intentional
instar-vs-qemu differences), cross-referenced from the refcount
repair section. quirks.md is where a user comparing the two tools'
output will look.

## Execution

| Step | Effort | Model | Brief |
|------|--------|-------|-------|
| 11a | medium | sonnet | The technical deep-dive docs, where accuracy matters most. Write the `## Repairing Refcount Inconsistencies` section in `docs/qcow2/qcow2-refcount.md` (two tiers, computed-refcount recount, crash-safe corrupt-bit ordering, refuse-don't-guess boundaries) and the `docs/quirks.md` entry for the `--repair=leaks` vs `qemu-img -r leaks` scope difference. **Ground every claim in the planner doc comments (`src/crates/check/src/qcow2.rs`) and the guest dispatch (`src/operations/check/src/main.rs`)** — read them, do not paraphrase from the plans. Add the `--repair` bullet to `docs/usage.md`'s `check` subsection. Verify the clap `--help` reads correctly (do not rewrite). |
| 11b | low | sonnet | The project-overview docs. `README.md`: add `--repair` to the `check` section and bump "22 fuzz targets" → "23". `ARCHITECTURE.md`: a short repair architecture note (planner crate + guest + crash-safe ordering), bump the fuzz count to 23, and name `fuzz_check_repair` in the prose. `AGENTS.md`: extend the `check` one-liner and add `tests/test_check_repair.py` to the test list. `docs/testing.md`: a `fuzz_check_repair` table row + a sentence on its coverage, and the `test_check_repair.py` suite + the `op_repair` differential operation. Keep counts consistent across all files (23 fuzz targets). |
| 11c | low | sonnet | Closeout + commit. `CHANGELOG.md`: an `[Unreleased]/Added` entry for `instar check --repair`. `docs/plans/index.md`: status row → Complete. `PLAN-convert-followups.md`: strike through the phase-2 row (Complete, pointer here). `PLAN-check-repair.md`: phase-11 row → Landed. Run `pre-commit run --files <the changed markdown files>` (scoped — the rust hook's `\.rs$` filter means a docs-only changeset never triggers clippy, so no ownership poison; do **not** use `--all-files`, which would). Present ONE commit on the `check-repair` branch documenting `check --repair` across the reference, project, and changelog docs, closing convert-followups phase 2. Confirm no source change ⇒ all binaries byte-identical. |

## Agent guidance

### Execution model

Sub-agents draft; the management session **fact-checks every
technical claim against the code** before committing — a doc that
misstates the safety model or the tier semantics is worse than no
doc. Pay special attention to: the crash-safe ordering (the exact
fsync sequence), the all-tier gate conditions (what makes instar
refuse), and the leaks-tier scope claim in quirks.md (it must match
`reclaim_leaks_in_refblock`'s "never lowers a live cluster's
refcount" contract).

### Model and effort notes

- **11a is medium sonnet**: the refcount deep-dive and the quirks
  entry are the load-bearing, must-be-correct docs.
- **11b / 11c are low sonnet**: overview prose, count bumps, and
  the changelog/index/strike-through closeout.

### Management session review checklist

- [ ] Every repair claim matches the code (planner contracts +
      guest dispatch), not just the plans.
- [ ] The safety model is stated correctly: tiered, **`check` is
      the safe default** (not "`--repair` dry-runs"), in-place, no
      mandatory backup, crash-safe corrupt-bit ordering, refuse-
      don't-guess.
- [ ] The quirks entry matches `reclaim_leaks_in_refblock`'s
      contract and names the `--repair=all` workaround.
- [ ] Fuzz-target count is **23** consistently across README,
      ARCHITECTURE, and testing.md, and `fuzz_check_repair` is in
      the testing.md table.
- [ ] `tests/test_check_repair.py` and the `op_repair` differential
      op are listed in the test docs.
- [ ] convert-followups phase 2 is struck through; index.md status
      is Complete; the master-plan phase-11 row is Landed.
- [ ] No source change ⇒ `check.bin` and `instar` byte-identical
      (if `--help` needed a wording fix, note the host rebuild and
      that `check.bin` is still untouched).

## Administration and logistics

### Success criteria

* `docs/usage.md`, `docs/qcow2/qcow2-refcount.md`, `docs/quirks.md`,
  `README.md`, `ARCHITECTURE.md`, `AGENTS.md`, `docs/testing.md`,
  and `CHANGELOG.md` document `check --repair` accurately.
* The fuzz-target count is 23 everywhere and `fuzz_check_repair` is
  in the testing table.
* convert-followups phase 2 is struck through; `index.md` status is
  Complete; the master plan's phase-11 row is Landed — closing the
  master plan.
* One docs commit on the `check-repair` branch; no source change,
  all binaries byte-identical.

### Future work created by this phase

None — this closes the master plan. The pre-existing future-work
items (VMDK/VHD/VHDX repair, refcount-table growth during repair,
`qemu-img amend`, snapshot-table structural repair, and the
guest→host repaired-counter wire follow-up) remain recorded in the
master plan's Future-work section, not here.

### Bugs fixed during this work

None expected (docs only). If fact-checking against the code
reveals a real behaviour that disagrees with the implemented intent
(not just a stale plan), that is a finding to surface to the
operator, not to silently document around.

### Documentation index maintenance

`index.md` status row → Complete; `order.yml` already carries the
master plan (no change). This phase plan is **not** added to
`order.yml`.

### Back brief

Before executing any step, back brief the operator on your
understanding — especially that this is a docs-only phase (all
binaries byte-identical), that every technical claim must be
verified against the code rather than the plans, and that the
`--repair=leaks` quirk and the crash-safe ordering are the two
claims most easily got wrong.
