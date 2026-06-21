# PLAN-amend phase 09: documentation

## Prompt

Before responding to questions or discussion points in this
document, explore the docs and the implementation thoroughly. Read
the analog command doc (`docs/resize.md` — the closest in-place
qcow2 metadata op; also skim `docs/rebase.md`, `docs/commit.md`),
the two parallel command tables (`docs/index.md`, `docs/usage.md`),
`README.md`, `ARCHITECTURE.md`, `AGENTS.md`, `CHANGELOG.md`, and
`docs/plans/index.md` / `docs/plans/order.yml`. Ground every
user-facing claim in what the code ACTUALLY does: the host
rendering (`render_amend_success` / `map_amend_error` /
`parse_amend_o_options` in `src/vmm/src/main.rs`), the result ABI
(`AmendResult` in `src/shared/src/lib.rs`), the planner's refusal
matrix (`src/crates/amend/src/qcow2.rs`), and the documented
divergence + behaviour in `tests/test_amend.py`. Do not invent flags,
output strings, JSON field names, or error wording — quote them from
the source. Flag uncertainty rather than guessing.

Phase plans live in `docs/plans/` named
`PLAN-amend-phase-NN-<descriptive>.md`. The master plan is
[PLAN-amend.md](/components/instar/plans/PLAN-amend/); phases 1–8 are landed (phase 8
also root-caused and fixed a core `.bss` overflow — see the master
plan's *Defects found during this work*). This is the ninth and
final phase.

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained.

## Situation

`instar amend` is implemented, tested (unit + integration +
cross-version baselines), and fuzzed (phases 1–8). It is the only
subcommand without user documentation. Every other subcommand has a
`docs/<cmd>.md` reference and rows in the parallel command tables;
amend is missing throughout. This phase writes amend's docs and
wires it into every place that enumerates the subcommand set, and
flips the master-plan status to Complete.

The grounding the implementer builds on (verified by the phase-9
docs survey):

- **Per-command doc template** is `docs/resize.md` (~285 lines):
  H1 title + one-line intro; a short intro para (what it does, how
  it aligns to `qemu-img`, the key constraint); `## Synopsis`
  (usage block with options + positional); a target/transition
  table; an output-format section (human one-liner matching qemu +
  a JSON example); semantics sections for the non-obvious flags;
  a `## Known divergences from qemu-img amend` bullet list; a
  `## Future work` list; and a `## Examples` block (simple →
  compound). `docs/amend.md` should mirror this shape.
- **Two parallel command tables** — `docs/index.md` and
  `docs/usage.md` both carry the same "instar features" table
  (rows like `| [Resize](/components/instar/plans/resize/) | \`instar resize\` - … |`).
  Both need the SAME amend row in the same relative position
  (after the Snapshot row).
- **`README.md`** lists the per-subcommand doc links in its Project
  Status section (add `docs/amend.md`) AND has per-operation Usage
  subsections (add a "QCOW2 Header Amendment" subsection with a few
  examples + a link to `docs/amend.md`, mirroring the Rebase/
  Snapshot subsections).
- **`ARCHITECTURE.md` and `AGENTS.md`** each enumerate the guest
  operations with a short paragraph per op (qcow2-only, the gates,
  a `docs/<cmd>.md` link). Add an `amend` entry after `snapshot` in
  both, matching the existing entry style.
- **`CHANGELOG.md`** is Keep-a-Changelog with an `## [Unreleased]`
  / `### Added` section; recent entries are bold-led and reference
  their PLAN. Add an amend entry there, and (under a `### Fixed`
  entry, creating the subsection if absent) note the core `.bss`
  overflow fix from phase 8 (it changed the guest memory layout —
  `OPERATION_LOAD_ADDR` 0x20000 → 0x22000 — and the size lint).
- **`docs/plans/index.md`** has the PLAN-amend master-plan row with
  a status column currently "Drafted, not started"; flip it to
  Complete. **`docs/plans/order.yml`** already lists PLAN-amend.md;
  phase files are NOT added to it — no change there.
- **Actual behaviour to document accurately** (verify in source,
  do not assume): the exact human success/no-op strings and the
  JSON field set from `render_amend_success`; the `-o` keys
  accepted by `parse_amend_o_options` (compat, lazy_refcounts) and
  that unknown keys are rejected; that amend is qcow2-only, needs
  `/dev/kvm` (launches a guest VMM), and rewrites only the header
  cluster; the refusal matrix (downgrade blocked by v3-only
  incompatible features DIRTY/CORRUPT/EXTERNAL_DATA/COMPRESSION/
  EXTENDED_L2, downgrade blocked by `refcount_bits != 16`, lazy-on
  requires v3, header-extension relocation unsupported); and the
  one documented divergence (instar refuses a zstd-compressed v3→v2
  downgrade that qemu-img 10.0.8 accepts — `KNOWN_AMEND_DIVERGENCES`
  context + master-plan *Compressed-image downgrade*).

## Mission and problem statement

After this phase:

1. **`docs/amend.md`** exists, modelled on `docs/resize.md`, and
   accurately documents the synopsis, the v2⇔v3 + lazy transition
   model, the `-o` options, human + JSON output (verbatim from the
   host renderer), the refusal/error matrix (verbatim wording from
   `map_amend_error`), the known qemu divergence, examples, and a
   future-work list (refcount_bits changes, data-file/encryption,
   backing-file-via-amend, non-qcow2 — from the master plan's Future
   work).
2. **Cross-references wired**: amend rows added to `docs/index.md`
   and `docs/usage.md`; `README.md` doc-link list + a Usage
   subsection; `ARCHITECTURE.md` + `AGENTS.md` operation entries;
   a `CHANGELOG.md` Added entry (amend) + Fixed entry (the phase-8
   `.bss` overflow fix).
3. **Status flipped**: `docs/plans/index.md` PLAN-amend row →
   Complete; the master plan `PLAN-amend.md` execution-table phase-9
   row → Complete and its `index.md` status note updated.
4. Docs build / link-check (whatever the repo runs) and
   `pre-commit run --all-files` are clean; `make instar` / tests are
   untouched (docs-only change).

Out of scope: documenting the separate pre-existing resize bug
(tracked as GitHub issue #373, not an amend concern); any code
change.

## Open questions

### 1. Table placement — after Snapshot, or grouped with metadata ops?

Recommendation (confirm): append the amend row AFTER the Snapshot
row in `docs/index.md` / `docs/usage.md` (chronological, matching
how late subcommands were added), and put the README Usage
subsection after "Internal Snapshots". Alternatively group amend
near resize/rebase/commit (the other in-place qcow2 metadata ops).
Confirm chronological-append vs metadata-grouping.

### 2. CHANGELOG: one amend entry, or amend + a separate `.bss`-fix entry?

Recommendation (confirm): TWO entries — an `### Added` bullet for
the amend subcommand (referencing PLAN-amend phases 1–9), and a
`### Fixed` bullet for the core `.bss` overflow that phase 8 found
and fixed (guest memory layout: `OPERATION_LOAD_ADDR` raised to
0x22000; the size lint now checks the `.bss`-inclusive extent). The
fix is independently meaningful (it corrupted every op's code at
0x20380) and deserves its own changelog line. Confirm.

### 3. How much qcow2-internals detail in docs/amend.md?

Recommendation (confirm): keep it user-facing like `docs/resize.md`
— explain WHAT changes (version, lazy flag) and the refusal rules
in user terms, with a brief "how it works" note (header-cluster
rewrite in a guest VM, no cluster/refcount data touched), but defer
deep header-format detail to the qcow2 crate docs. Confirm depth.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 9a | medium | sonnet | none | Write `docs/amend.md`, modelled section-for-section on `docs/resize.md`. Read `docs/resize.md` (structure/tone), `docs/rebase.md`/`docs/commit.md` (variations), and GROUND every fact in source: synopsis/flags from `parse_amend_o_options` + the clap `AmendArgs` in `src/vmm/src/main.rs`; the EXACT human success / no-op strings and JSON field names from `render_amend_success`; the error wording from `map_amend_error`; the AmendResult action/version/lazy fields from `src/shared/src/lib.rs`; the refusal matrix from `src/crates/amend/src/qcow2.rs` (downgrade feature blockers, refcount_bits!=16, lazy-requires-v3, extension-relocation-unsupported, dirty/corrupt); and the documented zstd-downgrade divergence from `tests/test_amend.py` + the master plan. Sections: title + intro (mirrors qemu-img amend; qcow2-only; rewrites header cluster in a guest VM, needs /dev/kvm); Synopsis; a v2⇔v3 + lazy transition/support table; Output (human one-liner + a real JSON example, both verbatim from the renderer — run `instar amend --output json` on a sample if needed to capture exact keys); `-o` option semantics (compat=0.10|1.1, lazy_refcounts=on|off, unknown keys rejected, at least one required); Known divergences from qemu-img amend (the zstd downgrade refusal, with the rationale); Examples (upgrade, downgrade, lazy on/off, combined `-o compat=1.1,lazy_refcounts=on`, a refused downgrade); Future work (from the master plan: refcount_bits, data-file/encryption, backing-file-via-amend, non-qcow2, extension-relocation hardening). Do NOT add an amend.md link to `docs/plans/order.yml`. Validate: the doc has no broken relative links and every quoted CLI string/JSON key/error message matches the source. Do NOT commit. |
| 9b | medium | sonnet | none | Wire amend into every enumerating doc (Open questions 1 & 2 as confirmed): add the `\| [Amend](/components/instar/plans/amend/) \| \`instar amend\` - change qcow2 image options (compat version, lazy_refcounts) \|` row to BOTH `docs/index.md` and `docs/usage.md` (after the Snapshot row, identical text/position); add `[docs/amend.md](/components/instar/plans/docs/amend/)` to the README Project-Status doc-link list and a README Usage subsection ("QCOW2 Header Amendment") with 3–4 examples + a link, mirroring the Rebase/Snapshot subsections; add an `amend` operation entry after `snapshot` in BOTH `ARCHITECTURE.md` and `AGENTS.md`, matching the existing per-op entry style (qcow2-only, the gates, a `docs/amend.md` link); add a `CHANGELOG.md` `### Added` bullet for the amend subcommand (reference PLAN-amend phases 1–9, summarise the transition model + test/fuzz coverage + the qcow2-only/refcount_bits=16/feature-blocker gates) AND a `### Fixed` bullet for the phase-8 core `.bss` overflow (OPERATION_LOAD_ADDR 0x20000→0x22000, op code at 0x20380 was being clobbered by core's OUTPUT_DEVICE static, size lint now `.bss`-inclusive). Keep README/AGENTS/ARCHITECTURE entries consistent with `docs/amend.md`. Validate links resolve. Do NOT commit. |
| 9c | low | sonnet | none | Status + gates + commit. Flip the PLAN-amend row in `docs/plans/index.md` status column to Complete; update `PLAN-amend.md` — execution-table phase-9 row → Complete, and the `index.md`/status note at the bottom of the master plan per its *Documentation index maintenance* section. Confirm `docs/plans/order.yml` is unchanged (PLAN-amend.md already present; no phase files). Run `pre-commit run --all-files` (clean) and any docs/link lint the repo has; confirm `make instar` and the test binaries are untouched (docs-only). Stage and present ONE docs commit for 9a–9c (message per CLAUDE.md, `Prompt:`/`Signed-off-by`/`Assisted-By`/`Co-Authored-By`). Do not push. |

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. After each step the management session reads the
actual changed files, cross-checks every documented CLI string /
JSON key / error message / refusal rule against the source (docs
that drift from behaviour are worse than no docs), confirms the
parallel tables stayed in sync, runs the gates, and then commits /
retries / upgrades.

### Model and effort notes

- 9a/9b are sonnet: the work is well-bounded by the `docs/resize.md`
  template and the already-established facts, but ACCURACY against
  the source is the bar — the management review verifies it.
- 9c is mechanical; sonnet/low.
- When in doubt, skew to the more capable model — a user-facing
  reference that misstates a refusal rule or output field is a real
  defect.

### Management session review checklist

After the steps:

- [ ] `docs/amend.md` exists, mirrors the resize-doc shape, and
      every CLI flag, human/JSON output string, error message, and
      refusal rule matches the source (spot-check against
      `render_amend_success` / `map_amend_error` / the planner).
- [ ] The zstd-downgrade divergence is documented with its rationale
      (not silently omitted).
- [ ] `docs/index.md` and `docs/usage.md` carry the identical amend
      row in the same position; no other rows disturbed.
- [ ] README (doc list + Usage subsection), ARCHITECTURE, AGENTS,
      CHANGELOG (Added + Fixed) all updated and mutually consistent.
- [ ] `docs/plans/index.md` PLAN-amend status = Complete; master
      plan phase-9 row = Complete; `order.yml` unchanged.
- [ ] All relative doc links resolve; `pre-commit` clean; `make
      instar`/binaries untouched.

## Administration and logistics

### Success criteria

Phase 9 is complete when:

* `docs/amend.md` is a complete, accurate user reference modelled on
  `docs/resize.md`.
* amend is enumerated everywhere the other subcommands are
  (index.md, usage.md, README ×2, ARCHITECTURE.md, AGENTS.md,
  CHANGELOG.md Added + the phase-8 `.bss` fix under Fixed).
* `docs/plans/index.md` and `PLAN-amend.md` show the work Complete;
  `order.yml` unchanged.
* `pre-commit` clean; docs-only (no code/test/binary change).

### Future work created by this phase

- None for amend (this is the last phase). The separate resize bug
  is tracked as GitHub issue #373. If a follow-up lifts an amend v1
  restriction (refcount_bits changes, zstd-downgrade recompression,
  non-qcow2), update `docs/amend.md`'s Future work + Known
  divergences accordingly.

### Documentation index maintenance

This is a phase plan, not a master plan. It is **not** added to
`docs/plans/order.yml`. The master plan links to it from its
Execution table (already present). Step 9c performs the master-plan
status flip described in PLAN-amend.md's own *Documentation index
maintenance* section.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with it, including which files get the new
amend rows/entries, that `docs/amend.md` must be grounded
verbatim in the host renderer / error map / planner (not assumed),
and that `order.yml` is intentionally left unchanged.
