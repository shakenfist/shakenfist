# Format coverage phase 7: docs (qemu-img-parity axis)

Master plan: [PLAN-format-coverage.md](/components/instar/plans/PLAN-format-coverage/)

## Status: Complete (2026-07-20)

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (QCOW2, VMDK, VHD/VHDX, LUKS, KVM, virtio,
disk image formats), research as needed to give a confident
answer. Flag any uncertainty explicitly rather than guessing.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Phase 7 is the final phase of the format-coverage master
plan: the docs axis, plus the master-plan close-out. A
grounding survey (2026-07-20) of every doc the per-phase
docs steps touched found most of the documentation already
current — phases 1-6 each shipped their own docs step —
which narrows phase 7 to the genuinely unfinished work.

### Already current (do NOT duplicate)

* `docs/format-coverage.md`: the detection table, the
  conversion input/output tables (vdi/parallels/qcow/dmg
  rows present), the per-op resize/rebase/commit/dd tables,
  the Other Format Safety Checks table (incl. phase-6 QED
  row), the per-format test-image inventory, and Completed
  narrative items 18-22 covering phases 2-6.
* `docs/quirks.md`: six phase sections recording every
  parity/divergence fact this programme produced (phase 1
  at :2474, 2 at :2711, 3 at :2868, 4 at :3119, 5 at
  :3515, 6 at :4007 — line numbers as of the survey).
* `README.md` Supported Formats list (:16-28) — accurate,
  including the four read-only input rows.
* `ARCHITECTURE.md` — describes all four new crates
  (vdi/parallels/qcow1/dmg) and the chain-reader feature
  dispatch. No stale claims.
* `CHANGELOG.md` — all six phases have entries. The file's
  idiom adds no entries for docs-only changes, so phase 7
  adds none (recorded here as a decision).
* CLI strings — nothing stale: input format is auto-probed
  (no clap allowlist to update) and the output roster
  (raw/qcow2/vmdk/vpc/vhdx) is deliberately unchanged.

### Genuinely unfinished (the phase-7 scope)

1. **The qemu-img-parity axis is missing.** The master
   plan's mission item 5 and the success-criteria bullet
   "`docs/format-coverage.md` (or sibling) tracks coverage
   against qemu-img's real roster, not just oslo" are
   unmet: the document's purpose statement (:3-6) still
   declares an oslo-only charter, and parity information is
   scattered across seven per-op tables with **no
   consolidated op × format matrix** anywhere (the only
   op-per-row matrix in the repo is quirks.md's QED-only
   table at :4069).
2. **`AGENTS.md:56` is stale** — its Supported Formats
   list ("qcow2 ..., raw, vmdk, vpc, vhdx, luks") omits
   vdi, parallels, qcow (v1), and dmg entirely; it was
   never touched by the per-phase docs steps. The master
   plan's success criteria names AGENTS.md explicitly.
3. **vvfat is documented nowhere outside the master plan.**
   The success-criteria bullet "Bochs, cloop, vvfat (and
   QED ...) produce clean, tested, documented refusals" is
   satisfied for bochs/cloop (phase 1) and QED (phase 6),
   but vvfat appears in no user-facing doc. vvfat is a
   directory-backed qemu pseudo-format: it synthesizes a
   FAT filesystem over a host directory and has no on-disk
   single-file container, so there is no file for instar to
   detect or refuse (`qemu-img create -f vvfat` itself
   fails with "Format driver does not support creation" —
   already recorded in the master plan Situation). The
   docs owe a short recorded rationale, not code.
4. **Master-plan close-out**: Execution row 7, the
   success-criteria confirmation sweep, the programme
   retrospective, `docs/plans/index.md`, and this plan's
   findings.
5. Minor: `README.md:18`'s "Initial target formats:"
   heading wording predates the programme (the list under
   it is current); README omits any mention of the
   detection-only (bochs/cloop) and policy-refused (qed)
   formats.

### The sourcing rule (the load-bearing constraint)

Twice in this programme (phases 4 and 6) a sub-agent wrote
a plausible-but-wrong claim about qemu's capabilities, and
only empirical testing caught it (most recently: "qemu
cannot resize/rebase/commit QED" — it can, all rc 0).
Phase 7 produces a large parity matrix, which is maximally
exposed to this failure mode. Therefore:

**Every cell of the parity axis must either (a) cite a
recorded fact — a quirks.md section, an existing
format-coverage.md table row, or a phase plan's Findings —
or (b) be measured empirically before being written, by
running the actual op against an existing fixture with both
the built instar and qemu-img (host 10.0.11 and/or the
static per-version builds in instar-testdata). The step
report must list exactly which cells were measured fresh
and what the measurements were. No cell may be filled from
general knowledge of qemu.**

## Decisions

1. **Widen `docs/format-coverage.md`; do not create a
   sibling document.** The master plan allowed either. The
   per-op tables, the divergence records, the test-image
   inventory, and the oslo cross-validation section already
   live in format-coverage.md; a sibling would fork the
   audience and re-create the archaeology problem the axis
   exists to end.
2. **No CHANGELOG entry for phase 7.** The file's idiom
   records shipped behaviour per phase; docs-only changes
   have never had entries and phase 7 changes no behaviour.
3. **CLI strings unchanged.** Nothing is stale (see above);
   phase 7 is docs-only, no code or test changes.

## Design

Two steps complete the phase — and the master plan.

### 7a — the qemu-img-parity axis in format-coverage.md

* Widen the purpose statement (:3-6) to the dual charter:
  oslo.utils `format_inspector` parity (the original
  mission) AND coverage tracking against qemu-img's real
  format roster, pointing at the new axis section.
* Add a new top-level section, "qemu-img parity axis": a
  consolidated matrix with one row per format instar
  detects — raw, qcow2, vmdk, vhd/vpc, vhdx, luks, vdi,
  parallels, qcow (v1), dmg, qed, bochs, cloop, iso — and
  one column per subcommand surface, grouped to stay
  readable: read-side (info, check, convert-input, compare,
  dd, bench, map, measure), in-place (resize, rebase,
  commit, amend, snapshot, bitmap), output-side
  (convert-output/create/dd-output as a single column — the
  unchanged five-format roster). Use a compact legend, e.g.
  ✓ = supported with qemu parity; R‡ = instar refuses where
  qemu-img succeeds (recorded divergence); R= = instar
  refuses and qemu-img also refuses; — = not applicable.
  Every R‡ cell carries a footnote linking the quirks.md
  section (or format-coverage table) that records the
  divergence; wide tables must follow the file's existing
  conventions for readability.
* Apply the sourcing rule above to every cell. Cells with
  no recorded source (expected examples: the in-place ops ×
  vdi/parallels/qcow1/dmg/bochs/cloop; qemu's own behaviour
  on bochs/cloop fixtures) must be measured: run both
  binaries against the existing fixtures and record
  rc + message, then footnote the cell "measured 2026-07-20
  vs qemu-img <version>" in the step report (the doc
  footnote can cite the phase-7 plan findings).
* Add the vvfat subsection under the axis: why there is
  nothing to detect (directory-backed pseudo-format, no
  on-disk container), what qemu-img itself does, and that
  this satisfies the master plan's vvfat clause by
  documentation rather than code. Empirically re-verify the
  one checkable claim (`qemu-img create -f vvfat` failure
  mode) before quoting it.
* Cross-link: the axis section points at the six quirks.md
  phase sections as the underlying evidence records; the
  Documented Divergences section gains a one-line pointer
  to the axis.

### 7b — consistency fixes + master-plan close-out

* `AGENTS.md`: update the Supported Formats section to the
  real roster — write formats (raw/qcow2/vmdk/vpc/vhdx),
  luks (info + decrypting convert), the four read-only
  input formats (vdi, parallels, qcow v1, dmg), the
  detection-only formats (bochs, cloop), and the QED
  policy refusal — phrased per AGENTS.md's existing
  terse style, with a pointer to format-coverage.md's axis
  for the full matrix.
* `README.md`: fix the "Initial target formats:" heading
  wording (the programme is no longer initial); add a
  short line after the format list noting detection-only
  (bochs/cloop) and policy-refused (qed) formats with a
  link to docs/format-coverage.md. No other README changes
  (the list itself is current; the skills section already
  satisfies the standing requirement).
* Master plan (`PLAN-format-coverage.md`):
  * Execution row 7 → Complete (2026-07-20, commit hashes).
  * Success-criteria sweep: annotate each bullet with its
    satisfying evidence (the two previously-owing bullets —
    the parity-axis doc and the AGENTS.md update — now
    satisfied by 7a/7b; the vvfat clause satisfied by the
    documented rationale), in the same inline style phase 6
    used for the QED clause.
  * A short close-out retrospective at the end of the plan
    (the qcow2-write-infrastructure precedent): programme
    summary — four new no_std crates with fuzz targets,
    four formats graduated to read paths, one recorded
    policy refusal, detection parity closed against oslo's
    roster, `test_info_safe` growth 580 → 954, the
    integration suite zero-fail counts as recorded in the
    phase-5/6 findings, the seven bugs fixed along the way
    (issue #444, the qcow1 misdetection, the never-consumed
    encrypted flag, four instar-testdata defects) — all
    sourced from the phase plans' Findings sections, no
    re-running of suites.
  * Plan status header → Complete.
* `docs/plans/index.md`: phase-7 link check-marked; the
  format-coverage row's status → Complete (all 7 phases),
  with the one-paragraph outcome summary the index idiom
  uses for completed plans.
* This plan file: Status → Complete + findings from both
  steps (including the fresh-measurement list from 7a).
* Final consistency grep across README/ARCHITECTURE/
  AGENTS/docs for any claim the new axis contradicts.

## Out of scope

* Implementing anything in the master plan's Future work
  list (map/measure graduation, DMG codecs, in-place-op
  trailer probing, QED path (b), upstream qemu bug
  reports, etc.) — phase 7 records them, nothing more.
* Any code, test, fixture, or testdata change. Docs only.
* A CHANGELOG entry (Decision 2).
* Normalising refusal-message wording surfaced by the
  axis (same reasoning as phase 6).

## Step-level guidance

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | medium | opus | none | In the instar worktree: author the qemu-img parity axis per the Design — widen the format-coverage.md purpose statement, add the consolidated matrix + legend + vvfat subsection + cross-links. HARD RULE: every cell cites a recorded fact (quirks.md section / existing table / phase Findings) or is measured empirically against BOTH the built instar and qemu-img using existing fixtures (fixtures for every format are in instar-testdata; static per-version qemu-img builds live there too; host qemu-img is 10.0.11). No cell from general knowledge. Re-verify `qemu-img create -f vvfat` before quoting its failure mode. Report: the full matrix as written, per-cell source citations, the exact list of freshly-measured cells with rc/message evidence, and any recorded fact the measurements contradicted (do not silently correct a quirks record — report the conflict). No code/test changes; docs only. |
| 7b | medium | sonnet | none | Docs close-out per the Design: AGENTS.md roster update; README heading + detection-only/policy-refused note; master plan row 7 Complete + success-criteria evidence sweep + close-out retrospective (sourced from phase-plan Findings; do not re-run suites) + Status Complete; index.md row Complete with outcome summary; this plan file Status Complete + findings incl. 7a's fresh-measurement list; final consistency grep. Report files changed, every success-criteria bullet with its evidence pointer, and any contradiction found. |

Sequencing: 7a then 7b (7b's close-out cites 7a's axis).

## Findings: step 7a — the qemu-img parity axis (2026-07-20)

Landed as commit `de1c3bc` ("docs: add the qemu-img parity axis.").

* Widened `docs/format-coverage.md`'s purpose statement (:3-16) to the
  dual charter: oslo.utils `format_inspector` parity (met in full, the
  original mission) AND qemu-img roster coverage (the new mission),
  pointing at the new "qemu-img parity axis" section.
* Added the consolidated section: a legend (six symbols — ✓, ✓‡, R‡,
  R=, ~‡, —), three tables (read-side ops: info/check/convert/compare/
  dd/bench/map/measure × 14 formats; in-place ops: resize/rebase/
  commit/amend/snapshot/bitmap × 14 formats; output side: the
  unchanged five-format create/convert-output/dd-output roster), 16
  numbered notes, and a vvfat subsection.
* **Sourcing**: every cell either cites one of the six `docs/quirks.md`
  "Format-coverage phase N" sections (phases 1-6, cross-linked at the
  top of the axis section) or an existing format-coverage.md table, or
  was measured fresh on 2026-07-20 against the built instar and
  qemu-img 10.0.11 using existing instar-testdata fixtures. **Zero
  conflicts** were found between a fresh measurement and any existing
  recorded fact — the sourcing-rule's failure mode (a plausible-but-
  wrong claim about qemu's behaviour, as happened twice earlier in the
  programme) did not recur this phase.
* **Fresh-measurement highlights** (notes 2, 6, 8, 9, 10, 11, 12, 13,
  15, 16 in the axis carry a "Measured 2026-07-20" tag; the vvfat
  claim was also re-verified):
  * Instar-only capabilities where qemu-img refuses: VHD `check` (rc
    0 vs qemu-img's exit-63 "This image format does not support
    checks"), VHDX `map` (rc 0 vs qemu-img's rc 1 "File contains
    external, encrypted or compressed clusters"), and VMDK/VHD/VHDX
    `resize` plus VMDK `rebase` (qemu-img refuses all of these on
    every shipped version).
  * VDI `bench` — rc 0 parity (both tools), confirming VDI belongs on
    the same "convert/compare/dd/bench source" footing as parallels/
    qcow1/dmg rather than the older "convert/compare/dd source"
    wording (this is what motivated the 7b README consistency fix).
  * Bochs/cloop `measure` is an R‡ divergence (qemu-img measures both,
    rc 0; instar refuses both) while their `map` is an R= parity
    refusal (both refuse — qemu-img's own `map` fails with "File
    contains external, encrypted or compressed clusters").
  * ISO's one divergence across the whole read-side row is `bench`:
    instar refuses ("bench: unsupported input format") where qemu-img
    benches the raw container (rc 0); every other read-side op is
    parity via the #444 raw pass-through exemption.
  * LUKS non-decrypting ops (check/bench/map/measure, every in-place
    op) are R= — on a bare LUKS fixture with no key material, qemu-img
    cannot open it either, so neither tool's refusal counts as a
    divergence.
  * `qemu-img create -f vvfat /path/to/dir` re-verified against
    qemu-img 10.0.11: `Format driver 'vvfat' does not support image
    creation` — unchanged from the master plan's original Situation
    citation.
  * **Management independently re-verified three of the fresh
    measurements** during review (VHD `check` rc 0 where qemu-img
    refuses; VHDX `map` rc 0 where qemu-img rc 1; VDI `bench` both rc
    0) and found the axis's cells accurate as written.
* Docs-only diff: no code, test, or testdata files touched.

## Findings: step 7b — consistency fixes + master-plan close-out (2026-07-20)

* **`AGENTS.md`** (`:54-70`): replaced the stale Supported Formats
  section (which still read "qcow2 ..., raw, vmdk, vpc, vhdx, luks"
  and omitted vdi/parallels/qcow1/dmg entirely) with the real roster
  in the file's terse style — write formats, luks, the four read-only
  input formats, the two detection-only formats, the QED policy
  refusal — with a pointer to `docs/format-coverage.md`'s parity axis.
* **`README.md`**: changed the "Initial target formats:" heading
  (:18) to "Supported formats:" (the programme is no longer initial);
  added a line after the format list (:30) noting bochs/cloop
  (detection + info only) and qed (policy-refused) with a link to
  `docs/format-coverage.md`.
  * **Consistency fix verified, not blindly applied**: the VDI line
    (:25) read "convert/compare/dd source" while the parallels/qcow/
    dmg lines already read "convert/compare/dd/bench source". Checked
    `tests/test_bench.py` directly rather than trusting the brief:
    `test_bench_vdi_simple` (`:2053-2076`) asserts `instar bench -f
    vdi vdi-simple` exits 0 with header parity against `qemu-img
    bench` on the same fixture — a live, passing pin, not aspirational
    — and the Makefile's qcow2 test-feature line
    (`cargo test --release -p qcow2 --features
    "create,vdi-input,parallels-input,qcow1-input,dmg-input"`, `:514`)
    confirms `vdi-input` is a real, wired feature. Corrected the line
    to "convert/compare/dd/bench source" to match.
* **Master plan (`PLAN-format-coverage.md`)**:
  * Added a `## Status: Complete (2026-07-20)` header (this master
    plan had none; several sibling master plans — e.g.
    `PLAN-release-v0.2.md`, `PLAN-distro-matrix-ci.md` — carry one at
    the same position, so this follows an existing, if inconsistent,
    convention rather than inventing a new one).
  * Execution row 7 → Complete (2026-07-20, commit `de1c3bc` for 7a
    plus the 7b close-out commit).
  * Success-criteria sweep: every one of the twelve bullets now
    carries an inline **Satisfied**/**Satisfied by phase N** evidence
    annotation, in phase 6's inline style. The two previously-owing
    bullets (the parity-axis doc; AGENTS/README consistency) are now
    satisfied by 7a/7b; the vvfat clause is satisfied by 7a's
    documented rationale. All evidence is cited from phase Findings —
    no suite was re-run for this sweep.
  * Added a "Programme retrospective" section (before "Future work",
    following the `PLAN-qcow2-write-infrastructure.md` precedent): a
    seven-phase outcome table, the shipped-capability summary, the
    bugs-fixed list, and the verification posture.
* **`docs/plans/index.md`**: the format-coverage row's Status column →
  a full outcome-summary paragraph (matching the bench/qcow2-write-
  infrastructure completed-row idiom, which embeds the summary in the
  Status cell rather than the Intent cell) plus phase-7 link
  check-marked.
* **This plan file**: Status → Complete; these two Findings sections.
* **Final consistency grep** (README.md, ARCHITECTURE.md, AGENTS.md,
  `docs/*.md`, `docs/commentary/*.md`) — every hit and disposition:
  * `ARCHITECTURE.md` — already current (confirmed, not changed): all
    four new crates (`vdi`, `parallels`, `qcow1`, `dmg`) are correctly
    described with their read-path status.
  * `docs/index.md` (:72) — the Format Coverage doc-index row still
    described format-coverage.md as "Comparison with oslo.utils
    format_inspector, test coverage gaps" only, not mentioning the new
    qemu-img parity axis. **Fixed**: reworded to mention both the
    oslo.utils comparison and the qemu-img parity axis.
  * `docs/chain-config.md` (ImageFormat Values table, :56-68) — the
    table stopped at value 11 (`Luks`) and was missing rows 12-16
    (`VmdkDescriptor`, `Parallels`, `Bochs`, `Cloop`, `Dmg`) even
    though `src/shared/src/lib.rs`'s `ImageFormat` enum (which this
    table transcribes) has carried those variants since phases 1/3/5.
    **Fixed**: added the five missing rows, correctly describing
    Parallels and Dmg as graduated to full read support (phases 3/5)
    and Bochs/Cloop as still detection-and-info-only.
  * `src/shared/src/lib.rs` (:1559-1570) — **found but NOT fixed
    (out of scope: code, not docs)**: the enum doc comments on
    `Parallels = 13` and `Dmg = 16` still read "Detection and info
    only; no read path," which is now stale — both formats graduated
    to full convert/compare/dd/bench read support in phases 3 and 5
    respectively. This is a genuine contradiction the axis surfaced,
    but fixing it is a one-line code comment change outside this
    docs-only phase's scope (and outside phase 7's "no code changes"
    Decision). Flagged here for a trivial follow-up rather than
    silently left undiscovered.
  * No other stale claims found: `README.md`, `AGENTS.md`,
    `docs/quirks.md`, `docs/map.md`, `docs/testing.md`,
    `docs/technology-primer.md`, `docs/commentary/*.md`,
    `docs/usage.md`, `docs/security.md`, and `docs/configuration.md`
    were all checked; the non-format-coverage docs mentioning vdi/
    parallels/bochs/cloop/dmg either predate and are unaffected by
    this programme (CVE/usage-analysis context) or already carry the
    correct current status.
* **A count discrepancy caught while writing the retrospective**: this
  plan's own Design/Step-guidance text (§ Design 7b, step 7b's brief)
  says "four instar-testdata defects," but the master plan's "Bugs
  fixed during this work" section — the authoritative source this step
  was told to cite rather than re-deriving — records exactly three
  testdata-specific defects (parallels driver missing from the 6.x
  static builds; stale committed `profiles/`/`version-map.json`;
  `detect-profiles.py` corrupting regenerated profiles). Phase 6's QED
  baseline retirement is a separate, correctly-scoped cleanup executed
  per the phase-6 policy decision, not a "defect" in that section's
  sense. The retrospective records three testdata defects and notes
  the discrepancy rather than inflating the count to match the plan
  text's passing phrase.
* **Management-review addition (one stale claim the grep missed)**:
  three live docs still called QCOW1 "qemu's original deprecated
  format" (`README.md:27`, `ARCHITECTURE.md:252`, and
  format-coverage.md's narrative item 20). The phase-6 hand-off
  explicitly extended the corrected qemu-deprecation framing to
  qcow1, and a fresh sweep of every vendored qemu version's
  `deprecated.rst`/`removed-features.rst` in instar-testdata
  (2026-07-20) confirms no qcow-v1 entry exists in any of them. All
  three corrected to "qemu's original copy-on-write format,
  superseded by qcow2 but not formally deprecated by qemu". The
  quirks.md phase-4 section's own parenthetical (:3122) and the
  master plan's historical Situation table keep the original wording
  as historical record, per the phase-6 precedent (live docs
  corrected, historical records annotated rather than rewritten).
* No code, test, or testdata files were touched in this step; `git
  status --short` at hand-off shows only doc-file modifications.

## Verification (management-session review checklist)

- [x] Docs-only diff; no code, tests, or testdata touched.
      (One stale code comment found by 7b's grep was flagged
      in the Findings for follow-up, not changed.)
- [x] Every parity-axis cell has a citation or an explicit
      fresh measurement in the 7a report; spot-check cells
      against their cited quirks sections, and re-run at
      least two of the fresh measurements independently.
      (Management re-ran three: VHD check, VHDX map, VDI
      bench — all matched.)
- [x] Any measurement-vs-quirks conflict was surfaced, not
      silently patched. (Zero conflicts; the QED
      backingless-vs-overlay rebase/commit nuance was
      reported and resolved as non-conflicting.)
- [x] AGENTS.md roster matches reality; vvfat rationale
      present; charter statement widened.
- [x] Master plan: row 7 Complete, every success-criteria
      bullet carries evidence, retrospective present,
      status Complete; index.md consistent.
- [x] pre-commit clean; commit messages per conventions.

## Success criteria

Phase 7 — and the format-coverage master plan — are
complete when: format-coverage.md carries the dual charter
and the consolidated, fully-sourced op × format qemu-img
parity axis (including the vvfat rationale); AGENTS.md and
README are consistent with it; and the master plan is
closed out with every success-criteria bullet evidenced,
the retrospective recorded, and index.md updated.

## Back brief

Before executing, back brief the operator: confirm the
scope is docs-only (no code/tests/testdata), that the axis
lands inside format-coverage.md rather than a sibling doc,
that every matrix cell will be source-cited or freshly
measured under the sourcing rule, and that 7b closes out
the master plan (row 7, success-criteria sweep,
retrospective, index).
