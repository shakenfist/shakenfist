# Phase 10: documentation, CHANGELOG polish, follow-ups

Master plan: [PLAN-measure.md](/components/instar/plans/PLAN-measure/) ·
Previous phase: [PLAN-measure-phase-09-fuzz-differential.md](/components/instar/plans/PLAN-measure-phase-09-fuzz-differential/)

## Status: Not started

## Mission

Land the user-facing documentation and bookkeeping that closes
out the master plan:

1. New `docs/measure.md` — user guide for the `measure`
   subcommand. CLI surface, per-target output semantics,
   compatibility matrix vs qemu-img, known divergences,
   examples.
2. `docs/quirks.md` extension with a measure-specific section
   covering the bitmaps emission rule, `--image-opts`
   rejection, `-o help` deferral, raw-source SEEK_HOLE
   limitation, and the convert-vs-measure cushion semantics.
3. `docs/usage.md` examples added under the new measure
   sub-section so the existing usage-reference docs pick it
   up.
4. `docs/index.md` link to `measure.md` so the page appears
   in the navigation.
5. `README.md` feature mention so newcomers see the command
   exists.
6. `AGENTS.md` operations-list update (matches the operation
   entries already mentioned at the top).
7. `ARCHITECTURE.md` Format Support section — short
   "Measurable target formats" line.
8. `CHANGELOG.md` polish — consolidate the eight measure
   Unreleased entries into a coherent narrative that reads
   well at release time.
9. `docs/plans/PLAN-convert-followups.md` — strike `measure`
   from the seven-subcommand deferred list (it shipped).
10. `docs/plans/PLAN-measure.md` retrospective fields —
    capture future-work items discovered during execution.
11. `docs/plans/index.md` master-plan status — flip to
    `Complete`.

## Why this is its own phase

Phases 1–9 produced ~5 000 LoC of Rust, ~1 200 LoC of Python
tests, and ~50 000 baseline files. Phase 10 is the small
ribbon on top — but it's *its* own phase because:

1. The documentation needs the executed-and-verified state of
   the feature, not the planned state. Each prior phase
   sketched docs in its own commit message; phase 10 turns
   those sketches into a coherent user-facing surface.
2. The known-divergence list and future-work items are real
   commitments to track; consolidating them in one pass
   beats scattering them across nine phase plans.
3. Marking the master plan complete is the right
   acknowledgement that the measure feature is shipped.

## Architecture

### `docs/measure.md` (new file)

Sections:

- **Overview** — one paragraph: what `instar measure` does,
  who needs it, why it's drop-in compatible with `qemu-img
  measure` for the cases qemu-img supports.

- **Synopsis** — `instar measure [OPTIONS] [INPUT]` with the
  full flag surface listed in a `## Synopsis` block (same
  style `docs/usage.md` uses for other operations).

- **Target formats** table:

  | Target | Source-image mode | --size mode | qemu-img parity? |
  |--------|-------------------|-------------|------------------|
  | `raw` | Yes | Yes | byte-identical |
  | `qcow2` | Yes | Yes | byte-identical |
  | `vmdk` | Yes | Yes | instar-only |
  | `vpc` (VHD) | Yes | Yes | instar-only |
  | `vhdx` | Yes | Yes | instar-only |

- **Output format** — two examples (human, JSON) side by
  side, byte-identical to qemu-img for raw and qcow2.

- **`-o key=value,...` reference** — per-target honoured keys
  (mirrors the table in the phase 5 plan):
  - qcow2: cluster_size, compat, refcount_bits, extended_l2,
    lazy_refcounts, compression_type, preallocation
  - vmdk: subformat, grain_size
  - vpc: subformat (dynamic / fixed)
  - vhdx: subformat (dynamic only; fixed not yet supported),
    block_size
  - Rejected: backing_file, backing_fmt, data_file,
    data_file_raw, encrypt.* (future work)
  - Accepted-ignored: vmdk adapter_type / hwversion /
    toolsversion / zeroed_grain; vpc force_size /
    force_size_calc; vhdx log_size / block_state_zero

- **Known divergences from qemu-img** (cross-link
  `quirks.md`):
  - Raw sources with sparse on-disk extents: instar over-
    reports `required` because the raw scanner doesn't use
    SEEK_HOLE.
  - QCOW2 sources for a handful of real-world images: instar
    counts allocated bytes slightly differently (compressed
    cluster / extended-L2 subcluster edge cases).
  - VHDX sources: instar treats every BAT block as fully
    allocated.
  - VMDK multi-extent source layouts: instar's scanner
    doesn't propagate the extent map fully.
  - VHD legacy CHS-only sources: instar reports a slightly
    different virtual_size.

- **Future work**:
  - `--snapshot` / `-l SNAPSHOT` for QCOW2 internal
    snapshots.
  - `encrypt.format=luks` aware sizing.
  - Backing-chain composition.
  - SEEK_HOLE detection for raw sources.
  - VHDX source partial-block-state walk.

- **Examples** — five short usage examples:
  ```
  instar measure --size 1G -O qcow2
  instar measure --size 1G -O qcow2 -o cluster_size=64k
  instar measure -O raw mydisk.qcow2
  instar measure -O qcow2 --output=json mydisk.vmdk
  instar measure -O vhdx mydisk.qcow2     # instar-only target
  ```

Target length: ~150 lines. Concise. Cross-links rather than
duplicating content.

### `docs/quirks.md` extension

Add a new H2 section `## measure subcommand quirks` between
the existing classifier sections. Bullets:

- `--image-opts driver=qcow2,...` is **rejected** with a clear
  error. instar doesn't accept the descriptor-based source
  specification.
- `-o help` is **rejected** with a clear error. Use the
  `--help` output for the available individual flags.
- `bitmaps: 0` field in JSON output / `bitmaps size: 0` in
  human output is emitted **only** when target=qcow2 AND the
  source is a qcow2 v3 image (matches qemu-img behaviour
  exactly; instar's gate uses a 4+4 byte peek of magic +
  version).
- Convert-vs-measure size bounds for vmdk / vpc / vhdx
  targets: `instar convert -O <fmt>` output file size lies
  in `[?, fully_allocated + max(1 MiB, fully_allocated/16)]`.
  The lower bound is permissive because instar's parser
  scanners can over-report `allocated_bytes` (the
  divergences listed below); convert's zero-skipping can
  produce strictly less than `required` and that's not a
  bug. The upper-bound cushion absorbs the convert writer's
  per-block sector alignment slack.
- Five known scanner divergences from `qemu-img measure`:
  - Raw sources with on-disk sparse extents (SEEK_HOLE).
  - QCOW2 sources for some real-world images (compressed
    cluster / extended-L2 subcluster edge case).
  - QCOW2 sources with backing chains (instar reports the
    top layer only).
  - VHDX sources (every block reported as fully allocated).
  - VMDK multi-extent source layouts.
  - VHD legacy CHS-only sources (virtual_size differs by ~2
    MiB).

Each divergence cross-references the phase 7c skip-list and
`docs/measure.md` future-work section.

### `docs/usage.md` extension

Add a `### measure` section under the existing operations
listing. Mirror the structure of `### convert` (which is the
closest analogue): synopsis, three or four key examples,
link to `docs/measure.md` for the full reference.

### `docs/index.md` link

Add a single bullet under the operations / subcommands list
(or wherever similar docs are catalogued):

```
- [Measure](/components/instar/plans/measure/) — predict file size for a target format
```

### `README.md` mention

The README's features list / supported operations list (one
line per operation, with one-sentence description):

```
- `measure` — Predict file size for converting an image to a
  target format. Matches `qemu-img measure` byte-for-byte for
  raw and qcow2 targets; supports vmdk / vpc / vhdx targets
  that qemu-img cannot measure.
```

### `AGENTS.md` operations-list update

The repo guide has a "## Operations" enumeration that
mentions the five existing ones (info / copy / check /
compare / convert). Add `measure` at the end with a
one-sentence pointer to the user docs.

### `ARCHITECTURE.md` Format Support

Existing "Format Support" / "Supported Formats" section
mentions input/output capability per format. Add a short
"Measurable target formats" sub-bullet (or table column)
clarifying which formats `measure` can predict sizes for:

```
**Measurable target formats**: raw, qcow2 (qemu-img-parity),
vmdk, vpc (VHD), vhdx (instar-only — qemu-img does not
implement measure for these targets).
```

### `CHANGELOG.md` polish

The Unreleased section currently contains 8 measure-related
Added entries and 3 Changed entries scattered across the
phases. Polish into a tighter narrative:

- Consolidate the per-phase entries into 3–4 grouped bullets:
  - One for the new `instar measure` subcommand (the CLI
    surface and what targets it supports).
  - One for the supporting library and crate-level pieces
    (`crates/measure/`, the per-parser `scan_allocation`
    extensions, `MeasureConfig`/`MeasureResult` and the
    CallTable ABI bump).
  - One for the testing and fuzzing infrastructure
    (integration tests, baselines, coverage-guided fuzz
    targets, differential fuzz extension).
  - One for the bug fixes surfaced during the work
    (parse_memory_size T suffix, bitmaps emission gate).
- Keep individual `PLAN-measure-phase-NN-*` citations as
  hyperlinks at the end of each bullet so the per-phase
  attribution stays intact.

Do **not** delete the original detailed entries from git
history (this is a textual reorganisation in the Unreleased
section only).

### `PLAN-convert-followups.md` strike-through

Phase 1's Execution table lists seven subcommands deferred
from the convert effort: `create / map / measure / resize /
snapshot / rebase / commit`. Strike `measure` from that
list (or mark it `~~measure~~`) and add a one-line note
below pointing at `PLAN-measure.md` for the executed work.

### `docs/plans/PLAN-measure.md` retrospective

The plan template's "Success criteria" and "Future work"
sections were placeholders during phases 1–9. Phase 10 fills
them in:

- **Success criteria** — bullet list of what's verified:
  - All 10 phases complete and committed.
  - 345-test integration suite passes (209 pass, 136 skip
    for documented reasons).
  - 80-version cross-version baselines in
    `instar-testdata/expected-outputs/measure-*`.
  - 15 fuzz targets in nightly CI; differential fuzzer
    extended.
  - End-to-end byte-equality with `qemu-img measure` for
    raw and qcow2 targets across every qemu version 6.0.0
    through 10.2.0 (per the baseline matrix).
  - Round-trip size bounds for vmdk/vpc/vhdx hold within
    documented cushion semantics.

- **Future work** — promote the divergences and TODOs
  surfaced during execution:
  - SEEK_HOLE detection in the raw scanner (host-side; the
    no_std raw crate would receive an already-computed
    allocated_bytes from the VMM that did the
    `lseek SEEK_HOLE/SEEK_DATA` scan).
  - VHDX scanner partial-block-state handling.
  - VMDK multi-extent scanner sparse propagation.
  - QCOW2 scanner backing-chain composition (the existing
    chain machinery would feed multiple AllocationSummaries
    that the host or guest combines with shadowing).
  - QCOW2 scanner compressed-cluster / extended-L2
    subcluster overcount investigation.
  - `encrypt.format=luks` aware sizing (model the LUKS
    header overhead based on `encrypt.iter-time` and the
    cipher choice).
  - `-l SNAPSHOT` snapshot-targeted measurement (reuses
    convert's snapshot machinery).
  - `-o help` listing.
  - `--image-opts` parsing if any user requests it.

- **Bugs fixed during this work** — list the two real bugs
  the test/fuzz phases surfaced and fixed:
  - `parse_memory_size` missing T suffix (phase 7b).
  - Missing `bitmaps` field emission for qcow2 v3 sources
    (phase 7c).

### `docs/plans/index.md` final flip

Bump the row to `Complete (phases 1-10)` and add a final-
state hyperlink set.

## Open questions

1. **Should the future-work items become GitHub issues?**
   The user's GitLab/GitHub flow uses issues for tracked
   work, but the master plan didn't specify issue filing as
   a phase 10 deliverable. Recommendation: document in
   PLAN-measure.md and docs/measure.md (immutable plan
   artefacts), and let the user file issues at their
   discretion. The phase 10 commit messages can include
   "consider filing as issues" notes for the operator's
   review.

2. **Should `docs/measure.md` cross-reference
   `crates/measure/src/lib.rs` for the math?** The user
   guide should not duplicate per-format formulas; pointing
   readers at the crate's source is clearer than re-deriving
   the qcow2 fixed-point refcount loop in markdown. Yes,
   cross-reference.

3. **Should the CHANGELOG polish remove the per-phase plan
   citations?** No. They're load-bearing for anyone debugging
   the release: trace a behaviour back to its plan + commit.
   Keep the citations as hyperlinks at the end of each
   bullet.

4. **Should `README.md` get a "Recent additions" or "New in
   v0.3" section, or just the operations list mention?**
   The README convention seems to be feature lists, not
   release notes. Just add the operation to the list.
   Release narrative belongs in CHANGELOG.

5. **Should `docs/measure.md` mention the
   `KNOWN_SOURCE_SCANNER_DIVERGENCES` constant in
   `tests/test_measure.py`?** Useful pointer for anyone
   investigating why a specific test skips. Yes, link to
   it as the canonical list.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 10a | medium | sonnet | none | Create `docs/measure.md` per the "docs/measure.md (new file)" section above. ~150 lines, Markdown. Target structure: Overview, Synopsis, Target formats table, Output format, `-o` reference, Known divergences (cross-link `quirks.md`), Future work, Examples. Update `docs/index.md` to link `measure.md`. Update `README.md` operations / features list with the one-line measure entry. Run `pre-commit run --all-files`. Touch only those three files. |
| 10b | medium | sonnet | none | Extend `docs/quirks.md` with the measure-subcommand section per the "docs/quirks.md extension" section above. Extend `docs/usage.md` with a `### measure` block under the existing operations listing, mirroring the structure of the existing `### convert` block. Run pre-commit. Touch only `docs/quirks.md` and `docs/usage.md`. |
| 10c | low | sonnet | none | Update `ARCHITECTURE.md`'s Format Support section with the "Measurable target formats" line. Update `AGENTS.md`'s operations list to add measure with a one-sentence pointer. Update `docs/plans/PLAN-convert-followups.md` to strike measure from the seven-subcommand deferred list (replace with `~~measure~~ — shipped, see [PLAN-measure.md](/components/instar/plans/PLAN-measure/)` or similar; preserve the strike-through). Run pre-commit. Touch only those three files. |
| 10d | medium | sonnet | none | Polish `CHANGELOG.md` Unreleased section: consolidate the eight measure-related Added entries and three Changed entries into 3–4 grouped narrative bullets per the "CHANGELOG.md polish" section. Preserve the per-phase plan citations as hyperlinks at the end of each bullet. Fill in `docs/plans/PLAN-measure.md` Success criteria, Future work, and Bugs fixed during this work sections per the "PLAN-measure.md retrospective" section. Update `docs/plans/index.md` to mark the row as `Complete (phases 1-10)`. Run pre-commit. Touch only `CHANGELOG.md`, `docs/plans/PLAN-measure.md`, and `docs/plans/index.md`. |

Total: 4 commits.

## Out of scope for phase 10

- Filing GitHub issues for future-work items (user's
  discretion).
- Actually implementing any future-work item (each is a
  separate piece of work).
- Rewriting any of the per-phase plan files (they stay as
  historical artefacts).
- Editing the existing detailed CHANGELOG entries below
  Unreleased — phase 10 only touches the Unreleased section.
- Renaming or moving the existing `PLAN-measure-phase-NN-*`
  files.

## Success criteria

- `docs/measure.md` exists and is linked from `docs/index.md`.
- `docs/quirks.md` has a `## measure subcommand quirks`
  section.
- `docs/usage.md` has a `### measure` block.
- `README.md` mentions the operation.
- `AGENTS.md` operations list includes measure.
- `ARCHITECTURE.md` Format Support section mentions
  measurable targets.
- `CHANGELOG.md` Unreleased has 3–4 polished measure bullets,
  each with a plan-file hyperlink.
- `docs/plans/PLAN-convert-followups.md` strikes measure
  from the deferred list.
- `docs/plans/PLAN-measure.md` retrospective fields are
  filled in (Success criteria, Future work, Bugs fixed).
- `docs/plans/index.md` marks the row Complete.
- `pre-commit run --all-files` passes for all four commits.

## Risks and mitigations

- **`docs/measure.md` drifts from the actual CLI surface**.
  Mitigation: 10a's brief instructs the sub-agent to read
  `instar measure --help` directly and copy the surface from
  there. Future flag additions update `--help` automatically
  and the doc explicitly defers to it for the canonical
  list.
- **CHANGELOG polish loses information**. Mitigation: 10d's
  brief keeps the per-phase hyperlinks and only consolidates
  the *prose*, not the citations.
- **Future-work list is incomplete**. Mitigation: the 10d
  brief enumerates every divergence and TODO surfaced
  during execution (sourced from the phase 7c skip-list and
  the phase 5 plan's rejected-keys table). If a sub-agent
  spots an additional item during the polish, they add it.

## Back brief

Before executing any step, the executing agent should
back-brief: which files are being touched, what new content
they're producing, and what existing content they're
preserving. The reviewer should verify no implementation
files (Rust, Python, generated baselines) are touched in
phase 10 — it's purely textual.
