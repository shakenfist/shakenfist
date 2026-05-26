# PLAN-resize phase 13: documentation, CHANGELOG, follow-ups

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read the
existing user-facing documentation (`docs/create.md`,
`docs/measure.md`, `docs/usage.md`, `docs/quirks.md`,
`docs/index.md`, `docs/format-coverage.md`, `README.md`,
`AGENTS.md`, `ARCHITECTURE.md`, `CHANGELOG.md`) to understand
the per-subcommand documentation pattern. Read every phase 1–12
plan under `docs/plans/PLAN-resize-phase-*.md` to surface the
Future-work items that accumulated and need to land in a
consolidated section. Read `docs/plans/PLAN-convert-followups.md`
where `resize` is currently listed as deferred work.

Where a question touches on external concepts (qemu-img
documented behaviour, Keep-a-Changelog format), research as
needed to give a confident answer. Flag any uncertainty
explicitly rather than guessing.

This is a phase plan under `PLAN-resize.md`. Refer to that
master plan for overall context. Phases 1–12 shipped the
planner, the guest binary, the host CLI + preallocation post-
pass, the cross-version baseline matrix, the integration test
suite, and both fuzz harnesses. Phase 13 closes the loop on
documentation so users know the subcommand exists and what its
contract is.

## Mission

Update the project's documentation and changelog so that the
existence, contract, and known divergences of `instar resize`
are discoverable from every entry point a user might land on.
Concretely:

1. **New `docs/resize.md`** — full user-facing reference,
   modelled on `docs/create.md` and `docs/measure.md`. Covers
   the CLI synopsis, the `[+-]SIZE` end-spec grammar, per-
   format support and option flags, preallocation modes, the
   shrink-vs-grow contract, known divergences from
   `qemu-img resize`, and an explicit Future-work section
   listing every deferred item.
2. **CHANGELOG** — new `## [Unreleased] ### Added` entry
   for `instar resize`, mirroring the long-form shape of the
   existing `instar create` entry (per-phase pointers,
   feature surface summary, deferred-work pointer).
3. **AGENTS.md** — append `resize` to the Operations list,
   matching the shape of the existing `create` bullet.
4. **ARCHITECTURE.md** — add `operations/resize/` to the
   per-operation enumeration; document the new
   `read_output_sector` call-table primitive in the
   communication-protocol section so future operations
   (rebase, commit) know it exists.
5. **README.md** — add `resize` to the available-
   subcommands list and the operations/ subdir enumeration.
   Add a one-line pointer to `docs/resize.md` alongside the
   existing pointers to `docs/measure.md` / `docs/create.md`.
6. **docs/index.md** — add a resize entry to the doc
   table-of-contents.
7. **docs/quirks.md** — append a `## resize subcommand
   quirks` section after the existing `## create subcommand
   quirks` section, documenting the cross-tool asymmetry
   (vmdk/vhd/vhdx unsupported upstream), the deliberate
   sparse-format data-region preallocation divergence, the
   shrink+preallocation rejection, the metadata-on-raw
   rejection, the VHD CHS-rounding carry-forward, and the
   `Image resized.` literal-string output.
8. **docs/format-coverage.md** — add a `resize` column (or
   row, depending on the existing layout) reflecting the
   per-format support matrix from phase 4–6 / 9.
9. **docs/plans/PLAN-convert-followups.md** — strike
   `resize` from the deferred-subcommands list at lines 75
   and 93–94 (in the same shape as the existing
   `~~create~~` / `~~measure~~` markings).
10. **docs/plans/PLAN-resize.md** — add an explicit
    `## Future work` section between `## Open questions` and
    `## Execution` consolidating every "queued under Future
    work" mention from phases 2–12 into a single inventory.
    Mark phase 13 row complete in the execution table.

## What the survey turned up

- **`docs/create.md` (301 lines)** and **`docs/measure.md`
  (198 lines)** are the closest precedents. Standard
  sections: Synopsis, Target formats, Output format, `-o
  key=value,...` reference, Known divergences, Future work,
  Examples. `docs/resize.md` mirrors this shape with two
  resize-specific additions: a `[+-]SIZE` end-spec grammar
  subsection, and a per-format `--shrink` support matrix.
- **`CHANGELOG.md`** has a single `## [Unreleased]` block at
  the top with `### Added` already populated for `instar
  create` and `instar measure`. Phase 13's entry slots in
  alongside as a third long-form bullet. The create entry
  weighs ~60 lines including the per-phase pointer list;
  resize's entry will be similar.
- **`AGENTS.md` lines 59–78** is the Operations list. The
  create bullet is the right shape to mirror (target
  formats covered, what runs host-only vs in the guest,
  option-flag surface, preallocation modes).
- **`ARCHITECTURE.md` lines 247–263** enumerate the existing
  operations. Add `operations/resize/` here. The
  call-table primitive `read_output_sector` lands in the
  communication-protocol section at line 77ish — note it as
  a phase-7 addition, reusable by future in-place
  operations.
- **`README.md` line 30 + 532** has two subcommand mentions
  that need updating. Line 33's `docs/measure.md` /
  `docs/create.md` pointer line gets a `docs/resize.md`
  addition.
- **`docs/index.md`** is the table-of-contents. Add a
  one-line entry pointing at `docs/resize.md`.
- **`docs/quirks.md`** has dedicated sections for
  per-subcommand quirks (`measure subcommand quirks` at
  line 791, `create subcommand quirks` at line 875). Phase
  13 appends a `resize subcommand quirks` section in the
  same shape, covering:
  - **qemu-img can't resize vmdk / vpc / vhdx**. Phase
    10 confirmed across versions 6.0.0–10.2.0. Phase 11's
    consistency suite (`TestResizeConsistency`) is the
    substitute coverage surface.
  - **Sparse-format data-region preallocation
    divergence**. instar preallocates only the
    newly-appended file region (`[file_size_before,
    file_size_after)`); qemu preallocates the entire data
    region for the new virtual size. Documented in
    PLAN-resize-phase-09-preallocation.md as a deliberate
    divergence; full parity queued under Future work.
  - **`--preallocation=falloc|full` + `--shrink`
    rejection**. qemu silently accepts the combination and
    discards the flag; instar rejects with `resize:
    --preallocation=<mode> is meaningless when shrinking`.
    Deliberate divergence for user clarity.
  - **`--preallocation=metadata` on raw rejection**. qemu
    accepts-but-no-ops; instar rejects with `resize:
    --preallocation=metadata is not supported for raw`.
    Same rationale.
  - **qcow2 `--preallocation=metadata` planner gap**.
    instar's qcow2 grow planner returns
    `PreallocationUnsupported` (phase 2c deferred). qemu
    supports it. Master-plan Future work.
  - **VHD CHS-rounded virtual_size carry-forward**. The
    create-time rounding (already documented under
    `create subcommand quirks`) persists across resize.
    Cross-reference rather than restate.
  - **`Image resized.` literal-string output**. Matches
    qemu byte-for-byte on the default human surface;
    `--output=json` emits a structured envelope.
- **`docs/format-coverage.md`** — need to check the
  existing layout before deciding whether resize is a new
  column or a new row. Likely a row addition under each
  format heading, matching how create/measure entries are
  structured.
- **`docs/plans/PLAN-convert-followups.md` lines 75 + 93**
  list `resize` as a deferred subcommand. Strike with the
  `~~resize~~` markdown the existing entries already use
  for `~~create~~` and `~~measure~~`, with a pointer to
  `PLAN-resize.md`. Update the status column at line 75 to
  include `resize complete — see PLAN-resize.md`.
- **`docs/plans/PLAN-resize.md`** doesn't have a
  `## Future work` section header; "Future work" is
  referenced inline ~10 times across phases 2/4/5/6/9.
  Phase 13 consolidates into a single section so reviewers
  don't have to grep across phase plans. The items to
  consolidate are:
  - qcow2 `Preallocation::Metadata` (phase 2c)
  - vmdk shrink (phase 6)
  - vhd shrink (phase 4)
  - vhdx shrink (phase 5; no qemu upstream to mirror)
  - Sparse-format data-region preallocation (phase 9 —
    qemu parity for `falloc`/`full` on qcow2 / vhdx /
    vmdk / vhd-dynamic)
  - vmdk multi-extent subformats (twoGbMaxExtent*,
    monolithicFlat) — currently rejected as
    UnsupportedSubformat
  - Differencing VHD / VHDX as the *target* of a resize
  - `--object` / `--image-opts` host CLI flags (phase 8;
    currently rejected)
  - Tightening QCOW2_MAX_RESIZE_SCRATCH for non-default
    cluster sizes (phase 12 finding — 2 MiB clusters
    overflow the current 32 MiB scratch)
  - Planner-side defensive checks for inconsistent host
    inputs (phase 12 finding — VHDX planner can produce
    plans with `total_file_size=0` if the host passes
    impossibly small file sizes; not reachable from real
    callers but worth hardening)
  - Re-parse round-trip in `fuzz_resize_planners` (phase
    12 open question 1)
  - Curated seed corpus for `fuzz_resize_planners` (phase
    12 open question 8)
  - Populated-image differential coverage (phase 12 open
    question 3)
  - vmdk/vhd/vhdx differential coverage via libyal tools
    if they ever gain resize support (phase 12 open
    question 5)

## Algorithmic design

### `docs/resize.md` skeleton

Structurally mirrors `docs/create.md`. ~250 lines, with
these sections:

```markdown
# `instar resize` — change a disk image's virtual size

## Synopsis

  instar resize [OPTIONS] FILE [+-]SIZE[bkKMGTPE]

## End-spec grammar

  64M      absolute new virtual size
  +1G      additive: current_virtual_size + 1 GiB
  -512M    subtractive: current_virtual_size - 512 MiB

Suffixes: b=512, k/K=1024, M/G/T/P/E (binary). Absent
suffix is bytes.

## Target formats

| Target | Grow | Shrink | Preallocation modes | Notes |
|--------|------|--------|---------------------|-------|
| raw    | ✓    | ✓      | off, falloc, full   | host-only (truncate + post-pass) |
| qcow2  | ✓    | ✓ (--shrink) | off, falloc, full (metadata deferred) | L1+refcount grow |
| vmdk   | ✓    | reject | off                 | monolithicSparse only |
| vpc    | ✓    | reject | off                 | dynamic + fixed grow |
| vhdx   | ✓    | reject | off                 | dynamic; two-header protocol |

## Output format

  Image resized.

(matches qemu-img byte-for-byte). `--output=json` emits
a structured envelope:

  { "filename": "...", "format": "qcow2",
    "action": "grow", "old_virtual_size": 1048576,
    "new_virtual_size": 67108864,
    "new_file_size": 262144 }

`-q` suppresses both.

## `--shrink` semantics

[Per-format shrink rules — when --shrink is required, when
shrink is rejected outright (vmdk/vhd/vhdx), what happens
when --shrink + preallocation are combined.]

## Preallocation modes

[off / metadata / falloc / full — what each does, which
formats accept which, where instar diverges from qemu.]

## `-o key=value,...` reference

[Per-format honoured/rejected/deferred key matrix.
Matches create.md's shape.]

## Known divergences from `qemu-img resize`

- vmdk / vpc / vhdx unsupported by qemu — instar resize
  works (consistency-verified via TestResizeConsistency).
- Sparse-format data-region preallocation: instar only
  preallocates the appended file region; qemu preallocates
  the entire data region. See docs/quirks.md.
- --preallocation=falloc|full + --shrink: instar rejects
  for clarity; qemu silently accepts and discards.
- --preallocation=metadata on raw: instar rejects; qemu
  no-ops.
- qcow2 + --preallocation=metadata: instar rejects via
  planner; queued under Future work.

## Future work

[Mirror of the master-plan Future-work section.]

## Examples

  instar resize -f qcow2 my.qcow2 +1G
  instar resize -f raw --preallocation falloc my.raw 64M
  instar resize -f qcow2 --shrink my.qcow2 -512M
  instar resize -f vhdx my.vhdx 100G
  instar resize -f qcow2 --output=json my.qcow2 16M
```

### CHANGELOG entry shape

Mirrors the existing `instar create` entry: opening
sentence, then prose covering CLI surface, per-format
support, preallocation modes, the `[+-]SIZE` grammar,
the host vs guest split, the deferred-work pointer.
Closes with the per-phase pointer list (phases 1–13).
~50 lines total.

### Master-plan Future-work section

Inserted after `## Open questions` (line 629) and before
`## Execution` (line 761) in `docs/plans/PLAN-resize.md`.
~30 lines covering the inventory enumerated in §"What the
survey turned up". Each item gets a one-line description
+ a parenthetical phase-N pointer so reviewers can find
the originating context.

## Test surface

Documentation phase — no executable tests. The smoke
gates are:

- **Link rot check**: every `docs/...md` link in the
  edits resolves to a file that exists.
- **Spell-check**: at least a manual read-through; no
  automated spell-check infrastructure to defer to.
- **Markdown lint**: any pre-commit hook that runs over
  the doc tree should pass.
- **Phase-link consistency**: the CHANGELOG's per-phase
  pointer list links to the actual phase plan filenames
  (phases 1–13) — copy-paste from PLAN-resize.md's
  execution table to keep them in sync.

## Public API delta

None. Phase 13 is documentation only. No source files
change.

## Open questions

1. **Should `docs/resize.md` enumerate every error message
   the host CLI can emit?** The create / measure docs
   don't. Sticking to the contract surface (CLI grammar,
   per-format support, divergences) is enough; the
   integration tests at `tests/test_resize.py` pin the
   exact rejection strings. *Recommendation*: no error-
   string enumeration — over-documents implementation
   details that may shift.

2. **Should the CHANGELOG entry cite the integration-test
   coverage number (e.g. "114 tests, 83 pass + 31
   documented skips") and fuzz iteration counts?** The
   create entry doesn't cite raw numbers, just feature
   surface. *Recommendation*: match the existing tone;
   cite tests + fuzz as "covered by an integration matrix
   and a coverage-guided fuzz target" without numbers.

3. **Should `docs/format-coverage.md` get a resize column,
   or a resize row per format?** Need to look at the
   existing structure to decide. *Recommendation*: defer
   the choice until 13b examines the file; the change is
   mechanical either way.

4. **Should the Future-work section live in `docs/resize.md`
   or only in `docs/plans/PLAN-resize.md`?** Both. The plan
   is the canonical inventory; `docs/resize.md`'s Future-
   work section gives users a heads-up about gaps without
   forcing them to read a plan file. The two should stay
   in sync; the plan's is the source of truth.

5. **Should we update `docs/usage.md` (the upstream
   qemu-img usage analysis)?** No — that doc analyses how
   *callers of qemu-img* use the tool, not how instar's
   users should invoke instar. Resize is already mentioned
   in the per-caller tables at lines 11–13. Adding instar-
   specific content would muddle the doc's scope.

6. **Should we update `docs/testing.md`?** Check whether
   it enumerates the integration-test files. If yes, add
   `tests/test_resize.py`. If no, leave alone.

7. **Where does the `read_output_sector` ARCHITECTURE note
   land?** The communication-protocol section (line 77ish)
   describes the call-table primitives. Add a one-line
   mention there, plus a deeper paragraph in the
   operations/resize/ enumeration explaining why resize
   needed it.

8. **Should `PLAN-resize.md`'s Future-work section
   cross-reference the convert-followups roadmap?** No
   strong reason. The two roadmaps stay independent;
   readers find one or the other but not both at once.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 13a | medium | sonnet | none | Write `docs/resize.md` per the §Algorithmic design skeleton. Add the `instar resize` entry to `## [Unreleased] ### Added` in `CHANGELOG.md` mirroring the existing `instar create` entry's shape (~50 lines). Add the `resize` bullet to `AGENTS.md`'s Operations list (after `create`). Add `operations/resize/` enumeration to `ARCHITECTURE.md` and a one-line `read_output_sector` note in the communication-protocol section. Update `README.md`'s subcommand list (line 30) and operations/ subdir mention (line 532) to include `resize`; add `docs/resize.md` to the pointer line. Commit. Acceptance: every doc reads correctly, no broken links, the CHANGELOG's phase-pointer list matches `docs/plans/PLAN-resize.md`'s execution table verbatim. |
| 13b | small | sonnet | none | Cross-cutting updates: append a `## resize subcommand quirks` section to `docs/quirks.md` after the existing `## create subcommand quirks` section (covering the six items enumerated in §"What the survey turned up"). Add a resize entry to `docs/index.md`'s table of contents. Update `docs/format-coverage.md` to reflect resize support per format (decide row-vs-column after reading the file). Strike `resize` from `docs/plans/PLAN-convert-followups.md` at lines 75 + 93–94 using the existing `~~create~~`/`~~measure~~` markdown pattern and add a pointer to `PLAN-resize.md`. If `docs/testing.md` enumerates integration-test files, add `tests/test_resize.py`. Commit. Acceptance: no `docs/...md` link rot; the `~~resize~~` strikethrough is in the same shape as the existing `~~create~~`/`~~measure~~`. |
| 13c | small | sonnet | none | Master-plan housekeeping. Insert a `## Future work` section in `docs/plans/PLAN-resize.md` between `## Open questions` and `## Execution`, consolidating the 13 items enumerated in §"What the survey turned up". Mark the phase 13 row in the execution table Complete with a one-line pointer to the new docs ("docs/resize.md + CHANGELOG + AGENTS/ARCH/README + quirks/index/format-coverage entries; convert-followups deferral struck"). Commit. Acceptance: every Future-work item carries its originating-phase pointer; the execution table shows all 13 rows Complete. |

## Out of scope for phase 13

- Source-code changes (anything outside `docs/`, `*.md`,
  and `CHANGELOG.md`).
- Implementing any of the Future-work items themselves.
- Updating `docs/usage.md` (out of scope per open
  question 5).
- Reorganising existing per-subcommand docs to match a
  new shared template (the create / measure / resize docs
  drift in details; that's a separate consolidation
  effort).
- The actual `v0.3.0` release tag — phase 13 stages the
  CHANGELOG entry under `[Unreleased]`; the release tag
  is a separate operation the user drives.

## Success criteria for phase 13

- `docs/resize.md` exists and covers the §Algorithmic
  design skeleton's sections end-to-end.
- `CHANGELOG.md` has an `instar resize` entry under
  `## [Unreleased] ### Added` with per-phase pointers
  matching `PLAN-resize.md`'s execution table.
- `AGENTS.md`, `ARCHITECTURE.md`, `README.md`,
  `docs/index.md`, `docs/format-coverage.md` all mention
  resize in the same places they mention create / measure.
- `docs/quirks.md` has a `## resize subcommand quirks`
  section.
- `docs/plans/PLAN-convert-followups.md` shows
  `~~resize~~` struck from the deferred-subcommands list,
  with a pointer to `PLAN-resize.md`.
- `docs/plans/PLAN-resize.md` has an explicit
  `## Future work` section and shows every phase row
  Complete in the execution table.
- `pre-commit run --all-files` clean (catches
  trailing-whitespace and any markdown-lint hooks the
  project runs).
- No broken `docs/...md` links anywhere in the edits.

## Sub-agent guidance

Read these files before starting any step:

- `docs/create.md` (the structural precedent for
  `docs/resize.md` and the prose tone for the
  CHANGELOG entry).
- `docs/measure.md` (a second per-subcommand precedent
  with slightly different layout — useful for
  cross-checking which structural choices generalise).
- `docs/quirks.md` lines 791–995 (the existing
  per-subcommand quirks sections — same shape used for
  the new resize section).
- `CHANGELOG.md` lines 1–214 (the `[Unreleased]` block
  + the two existing long-form entries for the
  resize entry to mirror).
- `AGENTS.md` lines 59–78 (the Operations list).
- `ARCHITECTURE.md` lines 77–263 (communication-protocol +
  operations enumeration).
- `README.md` lines 28–35 + 528–535 (the two subcommand
  mention sites).
- `docs/index.md` (the table of contents).
- `docs/format-coverage.md` (decide row-vs-column after
  reading).
- `docs/plans/PLAN-convert-followups.md` lines 73–98 (the
  strikethrough pattern).
- `docs/plans/PLAN-resize.md` (the execution table at
  line 763 + every "Future work" mention to consolidate).
- `docs/plans/PLAN-resize-phase-09-preallocation.md`'s
  "Sparse-format data-region preallocation: deferred"
  section (canonical statement of the documented
  divergence to cite in `docs/quirks.md` and
  `docs/resize.md`).

The management session review checklist is the same as
prior phases: per-step `git diff` review (this time the
diff is doc-only so prose review is the substantive
work); skim each modified file end-to-end after the
edits land to confirm no orphaned references or stale
links; smoke is pre-commit only.

Phase 13 closes the PLAN-resize.md roadmap. Once it
lands, the next user-facing artefact is whichever
v0.3.0 release the user cuts; that's a separate
operation outside phase 13's scope.
