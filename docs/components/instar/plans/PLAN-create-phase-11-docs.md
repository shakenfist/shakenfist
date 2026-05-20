# Phase 11: documentation, CHANGELOG polish, follow-ups

Master plan: [PLAN-create.md](/components/instar/plans/PLAN-create/) ·
Previous phase: [PLAN-create-phase-10-fuzz-differential.md](/components/instar/plans/PLAN-create-phase-10-fuzz-differential/)

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (how `docs/measure.md`
is laid out, the `docs/quirks.md` "measure subcommand quirks"
section's shape, how `AGENTS.md` enumerates operations, how the
measure phase-10 docs commit consolidated the CHANGELOG
Unreleased entries, the format of `docs/plans/index.md`'s
master-plan table), and ground answers in what the code does
today. Where a question touches on external concepts (VHD CHS
geometry rounding, qcow2 refcount_order encoding, qemu-img info
auto-detection of fixed VHDs), research as needed. Flag
uncertainty explicitly rather than guessing.

## Status: Not started

## Mission

Close out the master plan by landing the user-facing
documentation and bookkeeping that turns the ten implementation
phases into a coherent shipped feature:

1. New `docs/create.md` — user guide for the `instar create`
   subcommand. CLI surface, per-target metadata semantics,
   compatibility matrix vs `qemu-img create`, known
   divergences, examples.
2. `docs/quirks.md` extension with a create-specific section
   covering the documented writer divergences (vhd CHS
   rounding, qcow2 refcount_bits hardcode, qcow2 compat
   hardcode, zstd accept-ignore, vhdx default block_size),
   the raw host-only-path exception, vhd-fixed footer-only
   metadata, backing-file resolution semantics, and the
   preallocation accept set.
3. `docs/index.md` link to `create.md` so the page shows up
   in navigation.
4. `README.md` operations-list mention (one line per
   operation; the create line currently exists in `AGENTS.md`
   but not the README).
5. `AGENTS.md` operations list polish — the existing
   `create:` bullet at line 68 already describes the
   subcommand; phase 11 confirms it's accurate after the
   ten-phase build-out.
6. `ARCHITECTURE.md` Format Support section — short
   "Creatable target formats" line.
7. `CHANGELOG.md` polish — the Unreleased section already
   has three coherent create-related bullets (subcommand,
   baselines, integration tests + fuzz). Phase 11 reviews
   them for accuracy after all ten phases, adds any missing
   facts (e.g. phase 9's send_create_result CallTable
   addition for the fuzz crate), and tightens the prose.
8. `docs/plans/PLAN-convert-followups.md` — strike `create`
   from the seven-subcommand deferred list (it shipped).
9. `docs/plans/PLAN-create.md` retrospective fields —
   capture future-work items and bugs surfaced during
   execution.
10. `docs/plans/index.md` master-plan status — flip from
    `Drafted, not started` to `Complete (phases 1-11)`.

## Why this is its own phase

Phases 1–10 produced the create subcommand end-to-end:
metadata emitters, the guest binary, the host CLI surface,
`-o` parsing, backing-file support, preallocation modes,
cross-version baselines, integration tests, coverage-guided
fuzzing, differential fuzzing. Phase 11 is the small ribbon
on top — but it's its own phase because:

1. The documentation needs the executed-and-verified state of
   the feature, not the planned state. Each prior phase
   added CHANGELOG / ARCHITECTURE blurbs in its own commit
   message; phase 11 turns those into a coherent user-facing
   reference.
2. The known-divergence list and future-work items are real
   commitments to track; consolidating them in one pass
   beats scattering them across ten phase plans.
3. Marking the master plan complete is the right
   acknowledgement that the create feature shipped.

This phase mirrors `PLAN-measure-phase-10-docs.md`'s shape;
that template worked well there and applies cleanly here.

## What the survey turned up

### `docs/measure.md` as a template

199 lines, structured Overview → Synopsis → Target formats
table → Output format → `-o` reference → Known divergences
→ Future work → Examples. `docs/create.md` mirrors this
exactly. The create-specific differences:

- The output is a *new file*, not a numeric report. The
  "Output format" section becomes brief — `Created: ...`
  human / `--output=json` structured / `-q` quiet — with
  the file's content described in the Target formats
  section.
- The "Known divergences" section is bigger than measure's:
  five categories of *writer* divergence from `qemu-img
  create` (vs measure's *scanner* divergences from
  `qemu-img measure`), plus the raw host-only-path
  exception.
- The `-o` reference enumerates each format's accepted /
  rejected / accept-ignored keys.

### `docs/quirks.md` has a "measure subcommand quirks"
section starting at line 791

```
## measure subcommand quirks
  - --image-opts driver=qcow2,... rejected
  - -o help rejected
  - bitmaps emission rule
  - Convert-vs-measure size bounds
  - Five scanner divergences
```

Add a parallel `## create subcommand quirks` section after
it. Bullets:

- **Raw create runs host-side**: open + ftruncate +
  optional posix_fallocate / zero-fill. No KVM guest
  involvement (single-code-path exception documented in
  PLAN-create open-question 6).
- **VHD virtual_size diverges by CHS rounding**: qemu-img
  rounds up to the next CHS-aligned multiple (legacy VHD
  geometry); instar emits exact bytes. Both are valid VHDs.
  Phase 8b's KNOWN_WRITER_DIVERGENCES lists every affected
  case. Future work.
- **qcow2 refcount_bits hardcoded to 16**: the writer
  emits `refcount_order=4` regardless of the user's
  `-o refcount_bits=...` value. Honoured values that don't
  diverge: 1, 8, 16 (smaller values still fit the 16-bit
  encoding). Future work.
- **qcow2 compat hardcoded to 1.1**: the writer never
  emits `compat=0.10` even if requested. Future work.
- **qcow2 compression_type=zstd accept-ignored**: the
  header records `zlib` regardless. Future work.
- **vhdx default block_size differs from qemu**: instar
  emits 8 MiB for virtual sizes ≤ 1 GiB; qemu-img always
  emits 32 MiB. Specify `-o block_size=...` explicitly to
  match. Future work.
- **VHD fixed subformat has footer-only metadata**: the
  500 KiB footer at end-of-file carries the entire
  metadata. `qemu-img info` without `-f` auto-detects
  fixed VHDs as `format=raw` because the file start has
  no magic. Pass `-f vpc` explicitly to surface the
  vhd format.
- **Backing-file path written verbatim**: matches qemu-img
  exactly. The user-typed path string lands in the new
  image's metadata; the host resolves the path relative to
  the new image's directory only for the open syscall.
- **Backing-file format inference**: requires either `-F
  FMT` or `-u` (unsafe). qemu-img has the same rule in
  newer versions. instar accepts the format hint
  verbatim; auto-detection from the backing file's first
  sector wins if the hint disagrees with the magic.
- **Preallocation accept set**: `off` (any format), `metadata`
  (qcow2 only), `falloc` / `full` (raw or qcow2). vmdk /
  vpc / vhdx reject non-`off` preallocation with a "future
  work" pointer.
- **One known internal check failure**: `instar create -f
  qcow2 -o refcount_bits=64` produces a file `instar
  check` rejects (header claims 64-bit refcounts, on-disk
  entries are 16-bit because of the refcount_bits
  hardcode). Documented in
  `tests/test_create.py:KNOWN_CHECK_FAILURES`.

Cross-references to `docs/create.md` future-work and the
phase-8b `KNOWN_WRITER_DIVERGENCES` constant follow each
bullet.

### `AGENTS.md` already mentions create

Line 68 in `AGENTS.md` already has:

```
- `create`: create a new empty disk image of a given format
  and size.
```

with a note about the raw host-only path. Phase 11 verifies
the bullet is accurate after the ten-phase delivery and
adds a one-line pointer to `docs/create.md` for the user
reference.

### `README.md` operations list

Doesn't currently mention `create` (only convert, info,
measure are explicitly enumerated in the features list).
Phase 11 adds the line:

```
- `create` — Create a new empty disk image. Matches
  `qemu-img create` for qcow2 / vmdk monolithicSparse /
  vhd dynamic+fixed / vhdx dynamic outputs, with
  backing-file references and preallocation modes; raw
  output is host-side `ftruncate` + optional preallocation.
```

### CHANGELOG state

Three current Unreleased entries:

1. **`instar create` subcommand** — comprehensive,
   well-cited with all ten phase hyperlinks. Reads well.
   Phase 11 reviews for any factual drift after the full
   build-out (none expected).
2. **Cross-version `qemu-img create` baselines** — phase 7
   only. Reads well.
3. **Integration test matrix + coverage-guided fuzz +
   differential fuzz** — three sub-bullets cumulatively
   covering phases 8/9/10. Reads well.

The CHANGELOG is already in good shape. Phase 11's polish
work is light: verify accuracy + add the small `send_create_result`
CallTable extension note from phase 9 to the test/fuzz
bullet (if not already there).

### `PLAN-convert-followups.md` strike-through

Line 75 contains the parent row for the seven-subcommand
deferred list. measure already strikes through; phase 11
adds `~~create~~ — shipped, see [PLAN-create.md](/components/instar/plans/PLAN-create/)`.

### `docs/plans/index.md` row

Line 24's row reads `Drafted, not started` for the create
master plan. Phase 11 flips to `Complete (phases 1-11)`.

### Future-work items to capture in PLAN-create.md retrospective

Sourced from open questions, phase plans, and divergences
surfaced during execution:

- Refactor convert to call into `crates/create/` for
  metadata emission (master-plan future-work item; defer
  to a follow-up phase once create has soaked).
- Multi-file VMDK subformats (`monolithicFlat`,
  `twoGbMaxExtentSparse`, `twoGbMaxExtentFlat`) — needs
  multi-output-device support in the call table.
- `--object OBJDEF` for LUKS encrypted create — pair with
  encrypted-create support.
- VHDX fixed subformat — not produced by qemu-img either;
  add if requested.
- Preallocation for vmdk / vpc / vhdx — each format needs
  its own BAT-population pattern plus the same host
  `apply_preallocation` post-pass that qcow2 already uses.
- `compression_type=zstd`-aware create (currently
  accept-ignored).
- `-l SNAPSHOT` parallel to measure (not implemented by
  qemu-img either; mentioned for symmetry).
- Atomic-rename safety for output file (write to
  `FILENAME.tmp`, rename on success).
- VHD CHS-geometry round-trip — instar emits exact bytes;
  matching qemu's rounding would close the phase-8b
  divergence.
- qcow2 refcount_bits != 16 round-trip — writer hardcodes
  refcount_order=4; needs the L1/L2/refcount math to
  parameterise over the user's choice.
- qcow2 compat=0.10 round-trip — writer hardcodes 1.1.
- vhdx default block_size matching qemu (32 MiB) for
  virtual sizes ≤ 1 GiB.
- `detect-profiles.py` collision fix in instar-testdata —
  the flat-copy logic at
  `copy_multi_bucket_version_to_profile` assumes case
  names encode the target. For create-info-json that's
  not true; profile-NN/<case>.stdout.txt collides across
  targets. Phase 8 reads the raw per-target bucket
  directly to sidestep; the fix is queued for the next
  testdata regeneration.

### Bugs surfaced during execution (for retrospective)

- **Phase 6c**: `CreateConfig.preallocation()` decoder
  existed but the host's `flags` assembly didn't set the
  bits. Caught and fixed during phase 6c.
- **Phase 7**: `detect-profiles.py` flat-copy collision
  for create output type — recorded as a known testdata
  bug; phase 8 sidesteps.
- **Phase 9a**: fuzz crate's mock `CallTable` was missing
  the `send_create_result` field that phase 2 of create
  added to `shared::CallTable`. Caught when phase 9
  introduced the first fuzz crate dependency on `create`;
  fixed in 9a.
- **Phase 9b**: fuzzer-found strict-equality assertion on
  `virtual_size` after re-parse. The fuzzer's input
  produced a non-grain-aligned virtual_size, the planner
  rounded up (documented vmdk behaviour), and the parser
  reported the rounded value. The harness's strict
  assertion was too strict — replaced with
  `parsed >= requested && parsed - requested < 512 MiB`
  in 9b.

## Architecture

### `docs/create.md` (new file)

Mirror `docs/measure.md`'s shape exactly:

```
# `instar create` — create a new empty disk image

`instar create` produces a new empty disk image in a target
format. It is the safe, sandboxed equivalent of
`qemu-img create`, with `instar info` output equivalent to
`qemu-img info` on a `qemu-img create`-produced image
(modulo a documented divergence whitelist).

## Synopsis

instar create [-f FMT] [OPTIONS] [-b BACKING [-F FMT] [-u]] \
              FILENAME [SIZE]

[full flag listing]

## Target formats

| Target | Subformats | qemu-img parity |
|--------|------------|-----------------|
| raw    | (host-only) | byte-equivalent (file size + zero-fill semantics) |
| qcow2  | n/a | info-equivalent (modulo refcount_bits / compat / zstd) |
| vmdk   | monolithicSparse, streamOptimized | info-equivalent |
| vpc    | dynamic, fixed | info-equivalent (modulo CHS virtual_size rounding) |
| vhdx   | dynamic | info-equivalent (modulo default block_size when unspecified) |

## Output format

[human / json / quiet]

## `-o key=value,...` reference

[per-format key tables, mirror measure.md]

## Known divergences from qemu-img

[five categories from KNOWN_WRITER_DIVERGENCES]

## Future work

[from the list above]

## Examples

[5-6 examples covering raw, qcow2 with options, vmdk with
subformat, vhd dynamic+fixed, vhdx with block_size,
backing-file usage]
```

Target length: ~200 lines. Concise. Cross-links rather than
duplicating content.

### `docs/quirks.md` extension

New `## create subcommand quirks` section after the
`## measure subcommand quirks` block (around line 791).
Per-bullet content as enumerated in the survey above.

### `docs/index.md` link

Add a single bullet under the operations / subcommands
list:

```
- [Create](/components/instar/plans/create/) — create a new empty disk image
```

### `README.md` mention

One line in the supported operations / features list per
the survey above.

### `AGENTS.md` operations-list verification

Verify line 68's `create:` bullet is still accurate after
all ten phases. If anything's stale (e.g. the bullet
predates phase 6's preallocation work), update; otherwise
leave alone and add a one-line pointer to `docs/create.md`.

### `ARCHITECTURE.md` Format Support

Existing "Format Support" subsection at line 409. Add a
short "Creatable target formats" sub-bullet:

```
**Creatable target formats**: raw (host-only), qcow2
(qemu-img info-equivalent), vmdk monolithicSparse +
streamOptimized, vpc dynamic + fixed, vhdx dynamic.
Backing-file references supported on qcow2, vmdk, vpc,
vhdx (matches qemu-img's permission set).
```

### `CHANGELOG.md` polish

Review the three create-related Unreleased entries for
accuracy after the ten-phase build-out:

1. **`instar create` subcommand** — comprehensive entry
   with all ten phase hyperlinks. Verify the prose
   reflects the shipped state (vhdx-as-backing fix from
   phase 5a; vmdk-from-vmdk parentCID fix from phase 5b;
   the `BACKING_SIZE_TOO_LARGE` ceiling check phrasing).
   Likely no edit needed.
2. **Cross-version baselines** — phase 7 only; reads well.
3. **Integration tests + coverage-guided fuzz +
   differential fuzz** — confirm phase 9's `send_create_result`
   CallTable addition is acknowledged (this was a latent
   gap that 9a fixed; minor but worth one phrase).

Do **not** delete any of the per-phase commit messages
from git history — phase 11 is textual reorganisation of
the Unreleased section only.

### `PLAN-convert-followups.md` strike-through

Replace `create / map / ~~measure~~ / resize / ...` with
`~~create~~ / map / ~~measure~~ / resize / ...` on line 75,
and update the per-subcommand subsection at line 88 from
"create — Create new empty disk images. Raw via host-side
..." to a shipped entry:

```
- ~~**create**~~ — Shipped. See [PLAN-create.md](/components/instar/plans/PLAN-create/).
```

### `docs/plans/PLAN-create.md` retrospective

Fill in the Success criteria, Future work, and Bugs fixed
during this work sections per the lists in the survey
above. The plan template's placeholders are at the bottom
of the file.

### `docs/plans/index.md` final flip

Line 24's row flips from `Drafted, not started` to
`Complete (phases 1-11)`. The phase pointer list at the
end of the row already enumerates all 11 phases.

## Open questions

These should be answered during execution; escalate to the
operator rather than guessing.

1. **Should the future-work items become GitHub issues?**
   The user's repo flow uses issues for tracked work, but
   the master plan didn't specify issue filing as a phase
   11 deliverable. Recommendation: document in
   `PLAN-create.md` and `docs/create.md` (immutable plan
   artefacts), and let the user file issues at their
   discretion. The phase 11 commit messages can include
   "consider filing as issues" notes.

2. **Should `docs/create.md` cross-reference
   `crates/create/src/lib.rs` for the metadata layouts?**
   The user guide should not duplicate the per-format
   header / L1 / BAT structures in markdown; pointing
   readers at the crate's source is clearer. Yes,
   cross-reference at the bottom of the doc.

3. **Should the CHANGELOG polish merge the three create-
   related Unreleased entries into one mega-bullet?** No.
   They cover semantically distinct deliverables
   (subcommand, baseline artefact, test/fuzz
   infrastructure); splitting reads better at release
   time. Mirror measure's three-bullet structure.

4. **Should `README.md` get a "Recent additions" or "New
   in v0.3" section, or just the operations list mention?**
   The README convention is feature lists, not release
   notes. Just add the operation to the list. Release
   narrative belongs in CHANGELOG.

5. **Should `docs/create.md` mention the
   `KNOWN_WRITER_DIVERGENCES` constant in
   `tests/test_create.py`?** Yes — useful pointer for
   anyone investigating why a specific test skips. Link
   to it as the canonical list (like measure docs link
   `KNOWN_SOURCE_SCANNER_DIVERGENCES`).

6. **Should `docs/create.md` cover the differential fuzzer
   picker bias?** No — that's implementation detail of the
   test infrastructure. The user guide stays focused on
   what the *command* does. The picker bias is documented
   inline in `scripts/differential-fuzz.py` and in
   `docs/quirks.md`.

## Public surface added in phase 11

- `docs/create.md` (new file, ~200 lines).
- `docs/quirks.md` — new `## create subcommand quirks`
  section (~50 lines).
- `docs/index.md` — one new bullet linking `create.md`.
- `README.md` — one new bullet under operations list.
- `AGENTS.md` — minor edit if line-68 bullet has drifted;
  one-line `docs/create.md` pointer added regardless.
- `ARCHITECTURE.md` — new "Creatable target formats" line
  under Format Support.
- `CHANGELOG.md` — minor polish on the three create
  Unreleased entries (accuracy check + send_create_result
  acknowledgement).
- `docs/plans/PLAN-convert-followups.md` — `~~create~~`
  strike-through.
- `docs/plans/PLAN-create.md` — Success criteria / Future
  work / Bugs-fixed fields filled in.
- `docs/plans/index.md` — row flipped to Complete.

No instar source code or test changes.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 11a | medium | sonnet | none | Create `docs/create.md` per the "docs/create.md (new file)" section above. ~200 lines, Markdown. Read `instar create --help` first to source the canonical flag list (do not paraphrase from the plan). Target structure: Overview, Synopsis, Target formats table, Output format, `-o` reference, Known divergences (cross-link `docs/quirks.md`), Future work, Examples. Update `docs/index.md` to link `create.md` in the operations / subcommands section. Update `README.md` operations / features list with the one-line create entry. Run `pre-commit run --all-files`. Touch only those three files. |
| 11b | medium | sonnet | none | Extend `docs/quirks.md` with the create-subcommand quirks section after the existing `## measure subcommand quirks` block. Source the bullet content from the "docs/quirks.md extension" section of this plan: raw host-only path, vhd CHS rounding, qcow2 refcount_bits hardcode, qcow2 compat hardcode, zstd accept-ignore, vhdx default block_size, vhd-fixed footer auto-detection, backing-file path verbatim + resolution semantics, backing format inference, preallocation accept set, one known check failure (qcow2 1G-rb-64). Each bullet cross-references `docs/create.md`'s future-work and the phase-8b `KNOWN_WRITER_DIVERGENCES` constant where applicable. Run pre-commit. Touch only `docs/quirks.md`. |
| 11c | low | sonnet | none | Update `ARCHITECTURE.md`'s Format Support subsection (line 409) with a "Creatable target formats" sub-bullet per the plan. Verify `AGENTS.md`'s `create:` line-68 bullet is still accurate after the ten-phase delivery (raw host-only path + preallocation + backing files); fix any drift and append a one-line `docs/create.md` pointer. Update `docs/plans/PLAN-convert-followups.md` line 75 to add `~~create~~` to the strike-through list (preserve measure's existing strike-through) and update the per-subcommand subsection (line 88) to a one-line shipped entry. Run pre-commit. Touch only `ARCHITECTURE.md`, `AGENTS.md`, and `docs/plans/PLAN-convert-followups.md`. |
| 11d | medium | sonnet | none | Polish `CHANGELOG.md` Unreleased section: review the three create-related entries for accuracy after the ten-phase build-out (phase 5a vhdx-as-backing, phase 5b vmdk-from-vmdk parentCID, phase 5c ceiling check, phase 6 preallocation accept set, phase 8 detect-profiles bug, phase 9 send_create_result CallTable addition). Likely the only edit is acknowledging the latter in the test/fuzz bullet ("Phase 9 also added the missing `send_create_result` field to the fuzz crate's mock CallTable — a latent gap from phase 2 that surfaced when the create crate became a fuzz-target dependency"). Fill in `docs/plans/PLAN-create.md` Success criteria, Future work, and Bugs fixed during this work sections per the lists in this plan's "What the survey turned up" subsections. Update `docs/plans/index.md` line 24 to mark the row `Complete (phases 1-11)`. Run pre-commit. Touch only `CHANGELOG.md`, `docs/plans/PLAN-create.md`, and `docs/plans/index.md`. |

Total: 4 commits.

## Out of scope for phase 11

- Filing GitHub issues for future-work items (user's
  discretion).
- Actually implementing any future-work item (each is a
  separate piece of work, some are their own multi-phase
  plans).
- Fixing the phase-7 `detect-profiles.py` collision bug in
  `instar-testdata` (queued for the next testdata
  regeneration; out of scope for instar-repo work).
- Rewriting any of the per-phase plan files (they stay as
  historical artefacts).
- Editing the existing detailed CHANGELOG entries below
  Unreleased — phase 11 only touches the Unreleased
  section.
- Renaming or moving the existing
  `PLAN-create-phase-NN-*` files.
- Refactoring convert to call into `crates/create/`
  (master-plan future-work item; separate follow-up).

## Success criteria

- `docs/create.md` exists, is linked from `docs/index.md`,
  and lists every CLI flag `instar create --help` reports.
- `docs/quirks.md` has a `## create subcommand quirks`
  section covering every entry in
  `tests/test_create.py:KNOWN_WRITER_DIVERGENCES` plus the
  one entry in `KNOWN_CHECK_FAILURES` plus the raw
  host-only-path exception.
- `README.md` mentions `create` in the operations list.
- `AGENTS.md` operations list includes a `docs/create.md`
  pointer.
- `ARCHITECTURE.md` Format Support section mentions
  creatable targets.
- `CHANGELOG.md` Unreleased has the three create entries
  factually accurate after the build-out.
- `docs/plans/PLAN-convert-followups.md` strikes create
  from the deferred list.
- `docs/plans/PLAN-create.md` retrospective fields are
  filled in.
- `docs/plans/index.md` marks the row `Complete (phases
  1-11)`.
- `pre-commit run --all-files` passes for all four commits.

## Risks and mitigations

- **`docs/create.md` drifts from the actual CLI surface**.
  Mitigation: 11a's brief instructs the sub-agent to read
  `instar create --help` directly and copy the surface
  verbatim. Future flag additions update `--help`
  automatically and the doc defers to it.
- **CHANGELOG polish loses information**. Mitigation: 11d
  preserves the existing per-phase hyperlinks; the polish
  is prose-only.
- **Future-work list is incomplete**. Mitigation: 11d
  enumerates every divergence and TODO surfaced during
  execution (sourced from KNOWN_WRITER_DIVERGENCES,
  KNOWN_CHECK_FAILURES, the master plan's Future-work
  section, and the open questions in each phase plan).
  If a sub-agent spots an additional item during the
  polish, they add it.
- **AGENTS.md create bullet drift**: the bullet predates
  phase 6's preallocation work and may not mention it.
  Mitigation: 11c's brief says verify + update.

## Bugs fixed during this work

(To be filled in by 11d if anything surfaces during the
docs polish pass; otherwise empty.)

## Back brief

Before executing any step, the executing agent should
back-brief: which files are being touched, what new
content they're producing, and what existing content they're
preserving. The reviewer should verify no implementation
files (Rust, Python, generated baselines) are touched in
phase 11 — it's purely textual.
