# PLAN-bitmap phase 10: documentation

## Prompt

Before responding to questions or discussion points in this
document, explore the codebase and docs thoroughly. Read the actual
files and ground every claim in them — the per-subcommand guide
template `docs/amend.md`, the docs nav `docs/index.md`, `docs/usage.md`,
`README.md`, `ARCHITECTURE.md`, `AGENTS.md`, `CHANGELOG.md`,
`docs/resize.md`, and the actual host error strings in
`src/vmm/src/main.rs` (`map_bitmap_error`, `run_bitmap`,
`map_resize_error`). Do not speculate — **quote the verbatim error
messages and CLI surface from the code** so the docs are accurate.
Flag any uncertainty explicitly.

Phase plans live in `docs/plans/` as
`PLAN-bitmap-phase-NN-<descriptive>.md`. The master plan is
[PLAN-bitmap.md](/components/instar/plans/PLAN-bitmap/); Phases 1–9 are complete —
`instar bitmap` is functionally complete, integration-tested against
`qemu-img`, cross-version-baselined, and fuzzed. This is the tenth
and **final** phase: documentation. One commit per logical change.

## Situation

All code, tests, baselines, and fuzzing are done. This phase writes
the user-facing documentation and updates the project's index/nav/
governance files so `instar bitmap` is a first-class, documented
subcommand — closing the master plan. It is **docs-only** (no code).

The CLAUDE.md end-of-task mandate ("always update README.md,
AGENTS.md, ARCHITECTURE.md; if the project has a docs/ directory,
reflect changes there") is satisfied by this phase.

Grounding (verified against the phase-10 research, HEAD `14be112`):

- **No `mkdocs.yml`, no `CLAUDE.md`** at the repo root. The docs
  "nav" is `docs/index.md` (the "Instar-Specific Features" table,
  rows 43-54). The governance trio is `README.md` / `ARCHITECTURE.md`
  / `AGENTS.md` (+ `CHANGELOG.md`).
- **`docs/bitmap.md` does not exist** — it is the new guide. Template
  = `docs/amend.md` (298 lines, ~11.6 KB); peers `docs/dd.md` /
  `docs/snapshot.md` / `docs/resize.md` are the same ~11-13 KB class.
  Its section order: lede → `## Synopsis` (usage + Options block) →
  a semantic-matrix section → `## Output format` (human + JSON +
  field table) → per-action/option semantics → `## Refusals and
  errors` (verbatim-message table) → ``## Known divergences from
  `qemu-img bitmap` `` → `## Examples` → `## Future work` → footer
  links to the crate + the test whitelist.
- **`docs/usage.md`**: the platform matrices already list `bitmap`
  (lines 11/25/27/246/253) and the priority row (`:907`, "Low") —
  **no edit needed there**. The `#### bitmap operations` block
  (`:136-141`) documents *upstream qemu-img* as usage-analysis
  research — **leave as-is** (matching how `#### amend` `:130` was
  left). The real add is a **`### bitmap`** subsection in the
  `## instar Operations` section, after `### dd` (ends `:1000`),
  matching the `### amend`/`### dd`/`### snapshot` shape (synopsis
  block + one prose paragraph + a `See [docs/bitmap.md](/components/instar/plans/bitmap/)`
  line).
- **`README.md`**: add `docs/bitmap.md` to the per-subcommand-guide
  link list (`:33-37`), append `bitmap` to the operations sentence
  (`:30-31`), and add a `### ...bitmap...` usage section after the
  amend section (`:465`, before `### Version Compatibility` `:467`)
  in the same shape (bash examples + a docs-link paragraph). (The
  README "Directory Structure" ops line `:698` and AGENTS structure
  line are already stale re amend/dd — optional cleanup.)
- **`ARCHITECTURE.md`**: add an `- **operations/bitmap/** …` bullet
  after the `dd` operation bullet (`:555`, before `- **shared/**`
  `:556`), matching the amend/dd bullet shape (host/guest split,
  which crate provides the planner — `src/crates/bitmap/` — coverage
  summary, `See docs/bitmap.md`). Planner crates are described inside
  their op bullet, not in the shared-crate list (`:173-235`).
- **`AGENTS.md`**: add a `` - `bitmap`: … `` bullet to the
  `### Operations` list after `dd` (`:149`), and append `bitmap` to
  the `operations/` structure line (`:20`). (The test-file list
  `:303-361` already omits test_amend/dd/resize, so adding
  `tests/test_bitmap.py` is optional; there is no fuzz-target or
  differential-op enumeration to update.)
- **`CHANGELOG.md`**: Keep-a-Changelog; add a bullet to
  `## [Unreleased] → ### Added` (`:10`), mirroring the amend
  (`:107-118`) and dd (`:120-136`) bullets: `` - **New `instar
  bitmap` subcommand (PLAN-bitmap phases 1-10).** … See
  [docs/bitmap.md](/components/instar/plans/docs/bitmap/). ``. This is also where the
  **resize behaviour change** (now refuses bitmap-bearing images)
  belongs — as a sub-clause or a `### Changed`/`### Fixed` note.
- **`docs/index.md`**: add a `| [Bitmap](/components/instar/plans/bitmap/) | … |` row to
  the features table after the `Dd` row (`:54`).
- **`docs/resize.md`**: its divergences list (`:146-172`) does **not**
  mention bitmaps. Add a bullet (after `:172`, before the whitelist
  pointer `:174`): instar `resize` **refuses** qcow2 images carrying
  persistent dirty bitmaps (whereas qemu-img resizes them, dropping/
  invalidating the bitmaps). The refusal is implemented + tested
  (`tests/test_resize.py:658`); **quote the exact stderr from
  `src/vmm/src/main.rs` `map_resize_error` (ERROR_BITMAPS_
  UNSUPPORTED)** — do not paraphrase.
- **`docs/plans/order.yml`**: `PLAN-bitmap.md` is already listed;
  phase files are intentionally excluded — **no edit**.
  `docs/plans/index.md`: flip the master-plan status to *Complete*.

## Mission and problem statement

1. **Write `docs/bitmap.md`** (the comprehensive user guide,
   ~11-13 KB, mirroring `docs/amend.md`'s structure), covering:
   - What it does (sandboxed equivalent of `qemu-img bitmap`;
     manages qcow2 **persistent dirty bitmaps**; qcow2 **v3-only**;
     silent on success).
   - Synopsis + the full flag surface (the six repeatable
     CLI-order actions `--add`/`--remove`/`--clear`/`--enable`/
     `--disable`/`--merge SOURCE`; `-g`, `-f`, `--output`; the
     rejected `--object`/`--image-opts`/`-b`/`-F`).
   - Per-action semantics (add creates an enabled empty bitmap;
     the `auto` flag = enabled; the empty-bitmap representation;
     remove/clear/enable/disable/merge; merge is **same-file only**,
     source is a bitmap name), granularity (default = `min(65536,
     max(4096, cluster_size))`, power-of-two in [512, 2 GiB]), names
     (≤ 1023 bytes), the ordered action list, and the crash-safe
     autoclear behaviour (brief).
   - Output format (silent human; `--output json` envelope
     `{"actions-applied", "resulting-bitmaps"}`).
   - **Refusals and errors** — a verbatim-message table built from
     `map_bitmap_error` + the host validation in `run_bitmap`
     (not-qcow2, v2, already-exists, not-found, incompatible
     granularity, bad granularity, `-g` without `--add`, mixed
     merge, `--object`/`--image-opts`/`-b`/`-F` unsupported, refcount
     width, no space, …).
   - **Known divergences from `qemu-img bitmap`**: mixed
     merge+metadata refusal; cross-file `-b`; non-16-bit refcount;
     **cross-granularity merge** (instar refuses, qemu rescales);
     and the read-side note that **`instar info` does not list
     bitmaps** — use `qemu-img info` to inspect them. Point to
     `KNOWN_BITMAP_DIVERGENCES` / `KNOWN_BITMAP_DIFFERENTIAL_
     DIVERGENCES`.
   - **Interactions**: `instar resize` refuses bitmap-bearing images
     (cross-link `docs/resize.md`).
   - Examples (add, add -g, ordered `--add --disable`, remove, clear,
     enable/disable, same-file merge, json output, a couple of
     refusal transcripts).
   - Future work (cross-file merge, cross-granularity rescale,
     `instar info` bitmap listing).
   - Footer links to `src/crates/bitmap/`, `src/operations/bitmap/`,
     `tests/test_bitmap.py`.
   Register it in `docs/index.md`.

2. **Update the surrounding docs** per the edit checklist:
   `README.md` (link list + ops sentence + usage section),
   `docs/usage.md` (`### bitmap` subsection), `ARCHITECTURE.md`
   (operation bullet), `AGENTS.md` (ops-list bullet + structure
   line), `CHANGELOG.md` (Unreleased/Added bullet + resize note),
   `docs/resize.md` (resize-refuses-bitmaps divergence with the
   verbatim message).

3. **Close the plan**: mark the master-plan row/status *Complete* in
   `docs/plans/PLAN-bitmap.md` and `docs/plans/index.md`.

4. All content is **accurate to the code** — every error string,
   default, and limitation quoted/derived from the actual source,
   not from memory.

**Out of scope:** any code change; `docs/plans/order.yml` (already
correct); the usage.md upstream-research block.

## Open questions

### 1. Exact error strings + CLI surface

`docs/bitmap.md`'s "Refusals and errors" table and `docs/resize.md`'s
new bullet must use **verbatim** messages. Read `map_bitmap_error`
and the `run_bitmap` validation in `src/vmm/src/main.rs` for the
bitmap strings, and `map_resize_error`
(`ERROR_BITMAPS_UNSUPPORTED`) for the resize string. Read
`BitmapArgs`/`--help` for the exact flag surface. Do not paraphrase.

### 2. Depth of the autoclear / on-disk explanation

`docs/bitmap.md` is a *user* guide — keep the crash-safety/autoclear/
refcount internals to a brief sentence or two (or a "How it works"
aside), and point deep readers to `src/operations/bitmap/` and the
phase plans. Match `docs/amend.md`'s altitude (it explains the
header-patch model briefly, not exhaustively). Confirm.

### 3. Resize change: CHANGELOG placement

The resize-refuses-bitmaps fix landed on this branch. Put it in
`CHANGELOG.md` under `## [Unreleased]` — decide `### Changed` (it
changes resize's behaviour on bitmap images) vs a sub-clause of the
bitmap `### Added` bullet. Recommend a short `### Changed` bullet
(it is a behaviour change to an existing subcommand), cross-referenced
from the bitmap bullet. Confirm.

### 4. Optional stale-drift cleanup

The README Directory-Structure ops line (`:698`) and AGENTS
structure line already omit `amend`/`dd`. Fixing them to include
`bitmap` (and the missing siblings) is optional tidy-up — do the
bitmap-relevant part; note but do not gold-plate the pre-existing
drift. Confirm scope.

## Step-level guidance

Plan at **medium** effort — doc-writing, well-templated by
`docs/amend.md`, but `docs/bitmap.md` must be accurate and complete
(the user-facing surface), so review it against the code.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 10a | medium | opus | none | Write `docs/bitmap.md` (Mission §1), mirroring `docs/amend.md`'s structure and altitude. FIRST read the actual code for accuracy: `map_bitmap_error` + `run_bitmap` in `src/vmm/src/main.rs` (verbatim error strings), `BitmapArgs` + `instar bitmap --help` (flag surface), the granularity/name limits, and the `--output json` envelope keys (`render_bitmap_success`). Cover every section in Mission §1 with the real messages/semantics; include the qemu-vs-instar divergences (mixed merge, cross-file `-b`, non-16-bit refcount, cross-granularity merge, and "instar info lists no bitmaps — use qemu-img info"). Register the guide in `docs/index.md` (a row after the `Dd` row `:54`). Verify links resolve. |
| 10b | medium | sonnet | none | Update the surrounding docs (Mission §2) per the edit checklist: `README.md` (link list `:37`, ops sentence `:30`, a `### ...bitmap...` usage section after `:465`), `docs/usage.md` (a `### bitmap` subsection after `### dd` `:1000`, matching the amend/dd shape), `ARCHITECTURE.md` (an `operations/bitmap/` bullet after `:555`), `AGENTS.md` (a `bitmap` Operations bullet after `:149` + append `bitmap` to the `operations/` line `:20`), `CHANGELOG.md` (a bitmap `### Added` bullet at `:10` mirroring amend/dd + a `### Changed` bullet for the resize-refuses-bitmaps behaviour with the verbatim message), `docs/resize.md` (a divergence bullet after `:172` with the verbatim `map_resize_error` string). Keep each edit in the established local style; quote code strings verbatim. |
| 10c | low | sonnet | none | Close the plan + verify. Mark the phase-10 row and the overall master-plan status **Complete** in `docs/plans/PLAN-bitmap.md` (Execution table + any top-line status) and the master-plan row in `docs/plans/index.md` (status column → *Complete*, e.g. "Complete (phases 1-10)"). Confirm `docs/plans/order.yml` already lists `PLAN-bitmap.md` (no edit). Full gate: `pre-commit run --all-files` (docs hooks: trailing whitespace etc.), `make lint`/`make test-rust` (unaffected — confirm nothing regressed), and a manual pass that every new inter-doc link resolves. Present the commit(s). |

## Management session review checklist

- [ ] `docs/bitmap.md` exists, mirrors `docs/amend.md`'s structure,
      and every error string / default / limit is **verbatim from
      the code** (not memory); registered in `docs/index.md`.
- [ ] The divergences are documented (mixed merge, cross-file `-b`,
      non-16-bit refcount, cross-granularity merge, `instar info`
      lists no bitmaps).
- [ ] README (link list + ops sentence + usage section), usage.md
      (`### bitmap`), ARCHITECTURE (op bullet), AGENTS (ops bullet +
      structure line), CHANGELOG (Added + resize Changed) all updated
      in-style.
- [ ] `docs/resize.md` documents the resize-refuses-bitmaps
      divergence with the verbatim message; cross-linked from
      `docs/bitmap.md`.
- [ ] Master-plan status flipped to **Complete** in `PLAN-bitmap.md`
      + `index.md`; `order.yml` unchanged (already correct).
- [ ] No code changed; `make lint`/`make test-rust` unaffected;
      `pre-commit run --all-files` green; all inter-doc links resolve.
- [ ] Commit messages follow conventions.

## Success criteria

- `docs/bitmap.md` is a complete, code-accurate user guide, in the
  house style, registered in the docs nav.
- `README.md`, `docs/usage.md`, `ARCHITECTURE.md`, `AGENTS.md`,
  `CHANGELOG.md`, and `docs/resize.md` all reflect the new subcommand
  (and the resize behaviour change).
- The master plan is marked **Complete**; `instar bitmap` is a
  fully documented, first-class subcommand — leaving `bench` the
  only unimplemented qemu-img subcommand.
- `pre-commit run --all-files` green; no code touched.

## Back brief

Before executing any step, the executing agent should back brief the
operator — in particular that this is a **docs-only** phase, that
`docs/bitmap.md` must be **accurate to the code** (verbatim error
strings and CLI surface, read from `src/vmm/src/main.rs`, not
memory), that the key divergences (mixed merge, cross-file `-b`,
non-16-bit refcount, cross-granularity merge, and "`instar info`
lists no bitmaps") must be documented, that the **resize behaviour
change** (refuses bitmap-bearing images) is documented in
`docs/resize.md` + `CHANGELOG.md`, and that this phase **closes the
master plan**.
