# PLAN-dd phase 10: Documentation

Master plan: [PLAN-dd.md](/components/instar/plans/PLAN-dd/)
Previous phase: [PLAN-dd-phase-09-diff-fuzz.md](/components/instar/plans/PLAN-dd-phase-09-diff-fuzz/)

## Status: Complete

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Flag any uncertainty
explicitly rather than guessing.

## Mission

Document the completed `dd` subcommand and flip the master plan to
*Complete*. This is the final phase. Everything documented here is
already implemented and verified (phases 1–9) — the docs must match
the **actual** behaviour (the parity contract, the per-format
virtual-size rounding, the known divergences), not aspirations.

## What to write

### 1. `docs/dd.md` — the subcommand reference (new)

Mirror `docs/amend.md`'s structure (Title → Synopsis → Semantics →
Output format → Known divergences → Examples → Future work).
Content, all already-verified:

- **Synopsis.** `instar dd [-f FMT] [-O OUTPUT_FMT] if=INPUT
  of=OUTPUT [bs=N] [count=N] [skip=N]`. Both `if=`/`of=` mandatory;
  `name=value` operands; `-O` defaults to **raw** (not the input
  format).
- **Window semantics** (the [[dd-qemu-img-parity-contract]]): `bs`
  default 512, 1024-based suffixes, range `1..=INT_MAX` (`bs=0`
  rejected). `count` clamps the copy **down** only
  (`min(virtual_size, count*bs)`); `count=0` ⇒ empty. `skip`
  subtracts `skip*bs` from the front; skip past EOF ⇒ empty output,
  **exit 0**. Output starts at byte 0; ordering is count-then-skip.
  The copy is **dense** (no zero-skipping, unlike convert).
- **Output formats + size rounding** (verified vs qemu-img dd):
  raw, qcow2, vmdk, vpc (vhd), vhdx, default driver options only.
  Output file/virtual size = `round_up(out_vsize, 512)` for
  raw/qcow2/vmdk/vhdx; **VHD uses CHS geometry rounding** (larger;
  e.g. a 3000-byte window ⇒ 34816). Cross-validated byte- and
  size-identical to `qemu-img dd`.
- **Known divergences from `qemu-img dd`** (be honest — these are
  real):
  - **vhdx default block size**: instar emits 32 MiB blocks, qemu
    8 MiB for small images (a pre-existing instar vhdx-writer
    default; data and virtual size still match — only block-size
    metadata differs).
  - **`count=0 -O vmdk`**: `qemu-img dd` *itself* exits 1
    (monolithicSparse can't be 0-capacity); instar exits 0 with an
    empty (unreadable) vmdk.
  - **`count=0 -O vhdx`**: instar's 0-virtual-size vhdx is rejected
    by `qemu-img info` (qemu's empty vhdx is readable); no data,
    exit 0.
  - **`-f` (input format)**: accepted but forcing is deferred —
    auto-detection is authoritative.
  - error message *text* differs (instar-native), but exit codes
    and observable sizes match.
- **Out of scope (upstream parity only)**: the PVE/downstream
  operands `osize=`/`isize=`/`seek=`/stdin-stdout/`-n`/`-l`/
  `--target-image-opts`/`-o` create options, and `--image-opts`/
  `--object`/`-U` — list them so the boundary is clear.
- **Examples.** Whole-image to raw; `-O qcow2`; windowed
  (`skip=`/`count=`/`bs=`); a backing-chain input. Mirror the
  README and the integration-test cases (real, runnable).
- **Future work.** `-f` forcing; `--image-opts`/`--object`/`-U`;
  PVE extensions; making the empty `-O vhdx` qemu-readable.

Add a `| [Dd](/components/instar/plans/dd/) | `instar dd` — windowed block copy … |` row
to the `docs/index.md` doc-link table (next to Measure/Create/etc.).

### 2. Enumerations + cross-cutting docs

dd must be added everywhere the operation set is listed:
- **`README.md`**: the "Operations include `info`, `copy`, …" line
  (≈29) — add `dd`; and a new "### Block Copy (dd)" usage section
  after "### Image Conversion" (≈262), mirroring convert's example
  block (whole-image, `-O`, windowed, the `-O`-defaults-to-raw
  note).
- **`ARCHITECTURE.md`**: add `dd` to the operations description
  list (near the convert/measure/amend entries) — note it reuses
  `convert.bin` via the windowed `ConvertConfig`, the new
  `crates/dd` window-math crate, and the `read_chain_virtual_range`
  fix. Add the **`dd-info-json` cross-version baselines** to the
  baselines section (the note deferred from phase 7, alongside
  `create-info-json`).
- **`AGENTS.md`**: add `dd` to the `operations/` list (≈20) and a
  one-line `dd:` description in the per-command list (≈66–67).
- **`CHANGELOG.md`** (`[Unreleased]`): an **Added** entry for the
  `dd` subcommand (the whole feature — operands, window semantics,
  all output formats, qemu-img dd parity, the known divergences),
  and **Fixed** entries for the two phase-9 bugs (the convert
  sub-cluster data-loss fix `779e7a7` and the dense-VHD capacity
  fix `b80c5d7`) — the convert fix is a shipped-command fix, call
  it out.
- **`docs/format-coverage.md`** / **`docs/usage.md`**: check
  whether they enumerate per-subcommand support; add dd only if the
  doc's structure calls for it (`usage.md` is a survey of how
  downstream projects use qemu-img — likely no instar change, but
  confirm; if it has an "instar implements" note, update it).

### 3. Plan tracking

- `docs/plans/index.md`: flip the `PLAN-dd` *Master plans* row to
  **Complete** (phases 1–10).
- Verify the master plan's **Future work** captures every deferred
  item: PVE extensions, `--image-opts`/`--object`/`-U`, `-f`
  forcing, empty-vhdx readability, `fuzz_dd_operands`, and the
  **convert data-loss fix cherry-pick to develop**. Add any
  missing.
- Flip this phase's row + Status to Complete.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 10a | medium | sonnet | none | Write `docs/dd.md` mirroring `docs/amend.md`'s structure, with the content in §1 (synopsis, window semantics, output-format size rounding incl. VHD CHS, the four known divergences, out-of-scope list, runnable examples, future work). Ground every claim in the implemented behaviour — read `run_dd`/`compute_dd_window`/`chs_rounded_size` and the dd test files (`tests/test_dd.py`, `tests/test_dd_baselines.py`) so examples and divergences are accurate. Add the `docs/index.md` doc-link row. Verify internal links resolve and `pre-commit` (markdown hooks, if any) passes. |
| 10b | medium | sonnet | none | Update the enumerations + cross-cutting docs (§2): `README.md` (operations line + a "Block Copy (dd)" usage section mirroring the convert block), `ARCHITECTURE.md` (operations entry + the `crates/dd`/`convert.bin`-reuse/`read_chain_virtual_range` notes + the `dd-info-json` baselines note deferred from phase 7), `AGENTS.md` (operations list + `dd:` description), `CHANGELOG.md` (Added: dd subcommand; Fixed: the convert sub-cluster data-loss `779e7a7` and dense-VHD capacity `b80c5d7`). Check `docs/format-coverage.md` / `docs/usage.md` and update only if their structure enumerates per-command support. Keep each addition consistent with the surrounding style; do not restructure existing docs. |
| 10c | low | sonnet | none | Plan tracking (§3): flip the `PLAN-dd` row in `docs/plans/index.md` to Complete (phases 1–10); confirm/complete the master plan's Future-work list (PVE extensions, `--image-opts`/`--object`/`-U`, `-f` forcing, empty-vhdx readability, `fuzz_dd_operands`, convert-fix cherry-pick to develop); flip this phase's row + Status. Mechanical doc edits. |

Per the master plan / `PLAN-TEMPLATE.md`, sub-agents implement and
the management session reviews (read the rendered `docs/dd.md` —
confirm the divergences and rounding rules are stated correctly,
not aspirationally). Suggested commits: 10a `docs/dd.md`, 10b the
cross-cutting docs, 10c the plan-tracking flip (or fold 10c into
10b). After 10c the entire `PLAN-dd` effort is Complete.

## Verification

- [ ] `docs/dd.md` exists, mirrors the house subcommand-doc
      structure, and every behavioural claim (window math, size
      rounding, known divergences) matches the implementation +
      tests. `docs/index.md` links it.
- [ ] `dd` appears in the operations enumerations in `README.md`,
      `ARCHITECTURE.md`, and `AGENTS.md`; README has a dd usage
      section; `CHANGELOG.md` has the dd Added entry and the two
      phase-9 Fixed entries.
- [ ] `ARCHITECTURE.md` baselines section notes `dd-info-json`.
- [ ] `docs/plans/index.md` shows `PLAN-dd` Complete; master-plan
      Future work is complete (incl. the convert-fix cherry-pick).
- [ ] `pre-commit run --all-files` passes (markdown/actionlint/etc).
- [ ] Commit messages follow conventions (model/context/effort).
- [ ] No code changes — docs only.

## Completion

With phase 10 done, the `instar dd` subcommand is fully
implemented, tested (rust unit + integration + cross-version
baselines + coverage + differential fuzzing), and documented —
bringing instar to 14 of qemu-img's 15 subcommands (`bench` and
`bitmap` remain; see the master plan's Situation/Future work). The
[[dd-qemu-img-parity-contract]] memory records the verified rules
and the outstanding convert-fix cherry-pick.
