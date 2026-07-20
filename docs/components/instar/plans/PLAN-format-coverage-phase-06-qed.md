# Format coverage phase 6: QED decision

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

This phase resolves the master plan's Open question 1: does
QED get a read path (as VDI/Parallels/QCOW1/DMG did in
phases 2–5) or a principled, documented, fully-tested
refusal? Grounding was done 2026-07-19 by a combined
codebase survey + empirical pass (static qemu-img 6.0.0 /
8.2.0 / 10.2.0 + host 10.0.11; live git-master oslo.utils;
every instar op run against the qed fixture).

### Grounded facts

* **QED has an offset-0 header magic**
  (`format_detection.rs:170`), so — unlike DMG's
  info-op-only trailer probe — every probe path recognises
  it: the guest info op, the chain-discovery gate, AND the
  host prefix probes for the in-place ops. The empirical
  per-op audit found **zero dangerous cases**: all fifteen
  subcommands either work correctly (info, human and JSON,
  matching qemu byte-for-byte on the existing baselines) or
  refuse cleanly with a typed message and no file
  modification (verified by byte-hash after every mutating
  op). No raw pass-through, no crash, no silent-wrong
  output. The DMG-class audit hazard does not exist for
  QED.
* Refusal inventory: convert/compare/dd/bench refuse via
  the issue-#444 chain gate ("input format 'qed' is
  detected but not supported for reading (detection and
  info only)" — including the mid-chain backing position,
  pinned); check exits 63 ("This image format (qed) does
  not support checks"); map/measure emit their own guest
  refusals; resize/rebase/commit/amend/snapshot/bitmap
  refuse via their qcow2/vmdk whitelists. Cosmetic
  inconsistencies only: resize/rebase render the Debug
  spelling "Qed" where others say "qed", and exit codes
  vary (63 vs 1) — wording quirks, not safety issues.
* **Test-pin coverage is incomplete**: only convert
  (incl. mid-chain), compare, dd, and the oslo divergence
  are QED-named pins. check, map, measure, resize, rebase,
  commit, amend, snapshot, bitmap, and bench have no
  QED-named tests — their refusals ride on generic
  whitelist behaviour.
* **QED is NOT formally deprecated in QEMU.** No entry in
  any deprecated.rst/removed-features.rst, no runtime
  warning on any op or version, and `qemu-img create -f
  qed` still succeeds on 10.2.0. The master plan's
  "(deprecated)" annotation overstates qemu's stance; there
  is no removal timeline forcing a decision. qemu-img
  reads, writes, checks, maps, measures, and benches QED
  normally (all rc 0, convert md5 version-stable).
* **oslo.utils explicitly bans QED**: `detect_file_format`
  returns a real QEDInspector, whose safety check then
  raises `SafetyCheckFailed: ... banned` ("This file format
  is not allowed"). Detected-then-refused, by policy — the
  strongest ecosystem statement available that no OpenStack
  workload will hand a QED image to a converter expecting
  success. Already recorded in test_oslo_crossval.
* Testdata: one fixture (`qed-simple`, 10 MiB virtual,
  **entirely unallocated** — weak coverage), manifest
  `skip_qemu_img: true` / `run_in_ci: false` — yet the tree
  still carries a full, stale (2026-02-03), UNCONSUMED
  80-version qemu-img/check/compare baseline set for it,
  predating the flag. An inconsistency either decision
  should reconcile.
* Path (b) sketch (preserved for a future revisit): a QED
  reader is qcow1-class work — 68-byte LE header
  (cluster_size power-of-two in [4 KiB, 64 MiB], table_size
  1..16 clusters, `features & ~QED_FEATURE_MASK == 0` as
  the readability gate with QED_F_BACKING_FILE /
  QED_F_NEED_CHECK / QED_F_BACKING_FORMAT_NO_PROBE the only
  known bits), two-level L1/L2 cluster-offset tables, no
  compression, no encryption, in-header backing name with a
  no-probe flag. A `chain::ImageFormat::Qed` variant plus a
  reader arm would flip convert/compare/dd/bench, exactly
  like phase 4. `qemu-img create -f qed` still works, so
  deterministic fixtures are generatable, and the (stale)
  baseline corpus is regenerable. Estimated effort: at or
  below phase 4.

## Decision

**QED remains read-refused, as deliberate policy** — option
(a), confirming the master plan's lean, with a corrected
rationale:

1. Demand is nil. QED was a short-lived qcow2 alternative
   that never saw wide deployment; no user demand for
   reading QED archives has surfaced during five phases of
   format work.
2. The ecosystem agrees. oslo.utils detects QED and then
   bans it outright; instar refusing reads aligns with the
   stance of the tooling instar cross-validates against.
   (For DMG/VDI/etc. instar deliberately reads what oslo
   cannot — but there oslo merely lacks an inspector; for
   QED oslo has one and refuses by policy.)
3. The current refusal is already complete and safe (the
   audit's zero-dangerous-cases result); finishing the job
   costs only test pins and documentation.
4. The rationale is NOT "qemu deprecates it" — qemu does
   not, and this phase corrects that claim in our docs. The
   divergence is instar's own scope choice: qemu-img
   converts/checks/maps/measures QED; instar refuses, in
   the same recorded-divergence class as map/measure on the
   phase 2–5 formats.

**Revisit criteria** (recorded so the decision is cheap to
reverse): a real user request to read QED input, or QED
images surfacing in a workload instar serves. The path-(b)
sketch above is the starting point; the phase 2–5 template
applies directly.

## Design

Two small steps complete the phase:

### 6a — refusal-hardening pins + testdata reconciliation

* Add QED-named refusal pins for every op that lacks one:
  check (exit 63 + message), map, measure, bench, resize,
  rebase, commit, amend, snapshot, bitmap — each asserting
  the exact current message/exit code and that the image
  file is byte-unchanged after mutating ops (the audit
  table in this plan is the source of truth for expected
  values). Place each pin in its op's suite following that
  suite's existing refused-format patterns. The cosmetic
  inconsistencies (the "Qed" Debug spelling in
  resize/rebase; check's 63 vs the others' 1) are pinned
  AS-IS with comments — normalising them would touch other
  formats' messages for zero user value.
* Testdata: delete the stale, unconsumed 80-version
  qemu-img/check/compare baseline trees for `qed-simple`
  (they contradict its `skip_qemu_img: true` manifest state
  and predate it; regeneration is trivial via
  generate-baselines.py if path (b) is ever taken). Remove
  `'qed'` from generate-baselines.py's supported_formats
  whitelists so a future unrestricted run cannot silently
  recreate them (comment citing this plan). The fixture
  itself, its manifest entry, and the oslo recording stay.
* No product-code changes at all in this phase.

### 6b — docs (the decision record)

* quirks.md: a "Format-coverage phase 6" section — QED
  read-refusal as policy (rationale + revisit criteria);
  the qemu-not-deprecated correction; the per-op
  refusal/divergence table (qemu rc-0 ops instar refuses);
  the cosmetic-inconsistency note.
* format-coverage.md: QED row updated to "refused by
  policy (phase 6)" with the divergence note; narrative
  entry.
* Master plan: Execution row 6 → Complete; a dated
  RESOLVED addendum under Open question 1 (the historical
  question text stays); the success-criteria QED clause is
  satisfied by this decision; future-work gains the revisit
  criteria pointer. index.md: phase 6 check-marked.
* This plan file: Status → Complete + findings.
* CHANGELOG: a short policy-decision entry. README only if
  it makes format claims phase 6 changes (grep first).

## Out of scope

* Any QED reader/parser work (path (b) — preserved as a
  sketch only).
* Normalising refusal-message casing or exit codes across
  formats.
* New QED fixtures (the refusal surface parses only the
  68-byte header via info, already fuzz-covered through
  fuzz_format_detect and the info-op fuzzing).

## Step-level guidance

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | medium | opus | none | Two parts. (1) In the instar worktree: add QED-named refusal pins per the Design bullet — one test per op lacking one (check/map/measure/bench/resize/rebase/commit/amend/snapshot/bitmap), each in its op's existing suite following that suite's refused-format precedent, asserting the EXACT messages/exit codes from this plan's Situation audit table (re-verify each empirically against the built instar before pinning — do not trust the table blindly), and byte-unchanged files after mutating ops. Run the touched suites (zero fail; isolated re-run before believing failures). (2) In instar-testdata (NO commits — management reviews): delete the qed-simple baseline trees under expected-outputs/*/ (qemu-img-human, qemu-img-json, check-human, check-json, compare-human — verify the exact set by find), remove 'qed' from generate-baselines.py supported_formats whitelists with a comment citing this plan, and confirm no instar test consumed the deleted files (grep + run test_info_safe end-to-end: count must be unchanged at 954). Report: pin list with verbatim pinned strings, suite counts, deleted-file inventory (count per tree), the whitelist diff, deviations. |
| 6b | medium | sonnet | none | Docs close-out per the Design: quirks.md phase-6 section; format-coverage.md row + narrative; master plan row 6 Complete + the dated RESOLVED addendum under Open question 1 + future-work revisit pointer; index.md; this plan file Status Complete + findings from 6a's report; CHANGELOG entry; consistency grep for stale QED claims (especially any "(deprecated)" language OUTSIDE the master plan's historical Situation table — correct those; leave the historical table text with the addendum handling the correction). Report files changed + stale claims found. |

Sequencing: 6a then 6b.

## Findings: step 6a — refusal-hardening pins + testdata reconciliation

* Landed as commit `3fd48e6` ("tests: QED-named refusal pins for ten
  ops.") in the instar worktree. QED-named pins were added for check,
  map, measure, bench, resize, rebase, commit, amend, snapshot, and
  bitmap — the ten ops this plan's Situation section identified as
  lacking one (convert incl. mid-chain, compare, dd, and the oslo
  divergence were already pinned before this phase). Every pinned
  message/exit code was re-verified empirically against the built
  instar before pinning, per the step brief, rather than trusted from
  this plan's audit table blindly:
  * `check` — exit 63, `This image format (qed) does not support
    checks` + JSON `format == "qed"`. QED's offset-0 magic IS seen by
    check's own probe, so — unlike DMG, whose check names "raw" —
    check correctly names "qed" here.
  * `map` — `source format unrecognised`, exit 1.
  * `measure` — `source image is unsupported format`, exit 1.
  * `bench` — refused via the issue-#444 chain gate
    ("error discovering backing chain" +
    "input format 'qed' is detected but not supported for reading
    (detection and info only)") with empty stdout. **Deviation from
    the plan's audit table**: bench's refusal has no `"bench:"`
    message prefix, unlike convert/dd's refusals.
  * `resize` — `format Qed is not supported for in-place resize`.
  * `rebase` — `format 'Qed' does not support rebase (qcow2 and vmdk
    only)`.
  * `commit` — `format 'qed' does not support commit (qcow2 and vmdk
    only)`.
  * `amend` — `only qcow2 images can be amended`.
  * `snapshot` — `snapshot: source is not qcow2` (qemu-img refuses
    non-qcow2 sources too).
  * `bitmap` — `not a qcow2 image`.
  * All ten refusals exit 1 except check's exit 63; all mutating ops
    assert byte-unchanged files after refusal. Two cosmetic
    inconsistencies were pinned AS-IS per the Design bullet's explicit
    instruction, not normalised: `resize`/`rebase` render the Rust
    `Debug` spelling `"Qed"` (capital Q) where `commit` and the others
    use lowercase `"qed"`; `check` exits 63 where every other refusal
    exits 1.
  * Ten suites ran zero-fail: `check_formats` 77, `map` 100, `measure`
    289, `bench` 81, `resize` 98, `rebase` 28, `commit` 26, `amend` 28,
    `snapshot` 94, `bitmap` 53 — all passed.
* **A plan-premise correction, found and reported per the step
  brief's instruction to stop on contradiction rather than proceed
  blindly**: this plan's Situation section called the `qed-simple`
  qemu-img/check/compare baseline set "unconsumed." That claim was
  wrong in two ways, both confirmed empirically: (1)
  `test_info_safe` enumerates every `safety == "safe"` image and ran
  14 active `qed-simple` scenarios against the
  `qemu-img-{human,json}` baselines. (2) `detect-profiles.py`
  regenerates `profiles/` FROM `raw/`, so the info raw trees are
  transitively a source of truth, not dead weight. Management revised
  the deletion scope in light of this: **keep** all
  `qemu-img-{human,json}` qed baselines (522 files — they back QED's
  one genuinely supported op, `info`, and its 14 pins), **delete
  only** the `check-human`/`check-json`/`compare-human` qed trees
  (729 files — permanently unconsumable now that instar policy-
  refuses `check`/`compare` on QED and always will under this
  decision).
* `instar-testdata/scripts/generate-baselines.py`: `'qed'` was
  removed from the `check`, `compare`, `measure`, and `map`
  `supported_formats` whitelists (all four are policy-refused ops
  under this phase's decision), each removal commented with a
  citation to this plan so a future unrestricted baseline run cannot
  silently recreate the deleted trees. `info`'s whitelist
  deliberately keeps no format restriction (unchanged) since `info`
  is QED's one supported op.
* Testdata commit `cecb16565a` ("Retire unconsumable qed check/
  compare baselines.") landed on `main` in instar-testdata, per
  management review (not a management-session commit from this
  worktree). Verified after deletion: `test_info_safe` still passes
  exactly 954/954 (unchanged), and `test_check_formats` still passes
  exactly 77/77 (unchanged) — confirming the deleted trees were
  genuinely unconsumed by any instar test.
* No product code changed in this step, matching the Design's "No
  product-code changes at all in this phase" constraint.

## Findings: step 6b — docs close-out

* Updated `docs/quirks.md` with a new "Format-coverage phase 6: QED
  read-refusal as policy" section: the policy rationale (nil demand,
  oslo.utils' detect-then-ban stance, the zero-dangerous-cases audit
  result), a per-op divergence table distinguishing genuine qemu-img
  divergences (convert/compare/dd/bench/check/map/measure/resize/
  rebase/commit) from the three ops where instar's refusal matches
  qemu's own refusal (amend/snapshot/bitmap), the
  cosmetic-inconsistency note (the `"Qed"` Debug spelling, the 63-vs-1
  exit code), and the qemu-not-deprecated correction.
* Updated `docs/format-coverage.md`: the QED row in the "Other Format
  Safety Checks" table now states the refused-by-policy status with a
  link to the quirks.md section; the QED Images fixture table's `Key
  Features` cell no longer calls QED a "Deprecated format test"; a
  new narrative item 22 (matching the style of items 18-21 for VDI/
  Parallels/QCOW1/DMG) records the phase-6 decision, the pin list, the
  cosmetic-inconsistency notes, the qemu-deprecation correction, and
  the testdata reconciliation.
* Updated the master plan (`PLAN-format-coverage.md`): Execution table
  row 6 → Complete (2026-07-20, commits `3fd48e6` instar /
  `cecb16565a` testdata); Open question 1's existing dated RESOLVED
  addendum extended with a sentence noting phase 6 executed the
  decision; the QED success-criteria bullet marked satisfied inline;
  a new future-work bullet points at this plan's revisit criteria and
  path-(b) sketch.
* Updated `docs/plans/index.md`: phase 6 check-marked and the
  Format-coverage row's phase count moved from "1-5" to "1-6."
* Updated `CHANGELOG.md` with a short policy-decision entry under
  `[Unreleased]` / `### Added`, following the existing per-phase
  idiom (placed immediately before the phase 5 DMG entry, matching
  the file's newest-first ordering within that section).
* Consistency grep for stale QED claims outside this plan's own
  Situation table found two live-documentation instances of
  unqualified "(deprecated)" language, both corrected:
  `docs/chain-config.md`'s `chain::ImageFormat` enum reference table
  (`QED (deprecated)` → `QED (read-refused by policy, not because
  qemu deprecates it)` with a quirks.md pointer), and
  `docs/plans/PLAN-resize.md` (two spots: the per-format resize design
  section's `#### QED` entry, and a Future-work bullet), both of which
  asserted "the format is deprecated upstream" as the reason resize
  support was deferred — corrected to cite nil demand instead, with a
  pointer to this phase's finding that qemu does not deprecate QED.
  No stale QED claims were found in `README.md` (QED is not mentioned
  there) or `ARCHITECTURE.md` (its one QED mention, "QED banning" in
  the oslo.utils cross-reference, was already accurate and required
  no change). The master plan's own historical Situation-table text
  (`PLAN-format-coverage.md`'s "Situation" section, which listed qcow
  and qed as "(deprecated)" while surveying qemu-img's format roster)
  was deliberately left unchanged, per the step brief — the dated
  RESOLVED addendum under Open question 1 handles that correction in
  context rather than rewriting the historical survey table.
* No product code, tests, or testdata were touched in this step —
  docs only, left uncommitted for management review per instructions.
* **Management-review correction to the 6b draft** (found by
  empirically testing qemu-img 10.0.11 against QED before commit):
  the draft's divergence table claimed resize/rebase/commit are "not
  supported by qemu either." Wrong — qemu-img resizes QED, rebases
  QED overlays, and commits QED overlays, all rc 0. Only
  amend ("Format driver 'qed' does not support option amendment"),
  snapshot -c ("Operation not supported"), and bitmap --add
  ("Operation not supported", no persistent-bitmap store) genuinely
  fail in qemu. The divergence set is therefore ten ops
  (convert/compare/dd/bench/check/map/measure/resize/rebase/commit),
  not seven; quirks.md, format-coverage.md, and this findings section
  were corrected before commit. This plan's own Situation/Hand-off
  text predates the measurement and words the divergence class
  loosely; the corrected table in quirks.md is authoritative.

## Verification (management-session review checklist)

- [x] No product code changed; only tests, testdata
      deletions, and docs.
- [x] Every op has a QED-named pin; suites zero-fail;
      test_info_safe count unchanged (954).
- [x] Deleted baselines were genuinely unconsumed; the
      whitelist guard prevents regeneration. (Scope revised
      during review: the info baselines turned out to be
      consumed and were kept — see the 6a findings.)
- [x] The decision record is honest about qemu's actual
      stance (not deprecated) and the divergences. (After a
      review correction: resize/rebase/commit ARE qemu-side
      rc-0 ops, hence divergences — see the 6b findings.)
- [x] pre-commit clean; commit messages per conventions.

## Success criteria

Phase 6 is complete when the QED decision (refusal as
policy) is recorded with rationale and revisit criteria;
every instar op's QED refusal is pinned by a named test;
the testdata inconsistency is reconciled; and the master
plan's Open question 1 is resolved.

## Hand-off to phase 7

Phase 7 (docs) inherits: the completed per-format
divergence inventory (map/measure scope refusals for
vdi/parallels/qcow1/dmg; check/map/measure/bench policy
refusals for qed; dmg capacity caps and codec refusals) as
the backbone of the qemu-img-parity axis table, plus the
corrected qemu-deprecation framing for qcow1 (also not
warned at runtime) and qed.

## Back brief

Before executing, back brief the operator: confirm the
decision (refusal as policy, not a read path — with the
corrected not-deprecated rationale and recorded revisit
criteria), that 6a adds pins and deletes the stale
unconsumed qed baselines in testdata (management-reviewed
before commit), and that no product code changes.
