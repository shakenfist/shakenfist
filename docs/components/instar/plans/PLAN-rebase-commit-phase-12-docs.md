# PLAN-rebase-commit phase 12: documentation, CHANGELOG, ARCHITECTURE

## Prompt

Before responding to questions or discussion points in this
document, explore the codebase thoroughly. Read
[docs/resize.md](/components/instar/resize/) and [docs/create.md](/components/instar/create/)
end-to-end — they're the structural twins for the new
per-subcommand user guides. Read the existing
`CHANGELOG.md`'s `[Unreleased]` section (the resize entry
sits here today as the latest precedent for shape and depth),
the operations table at `README.md` line 26 (Project Status),
the `Usage` section at line 105, the `docs/index.md`
Operations table (line 41), the `docs/format-coverage.md`
"Resize Format Support" section (line 113), the
`docs/quirks.md` "resize subcommand quirks" section (line
996), and `ARCHITECTURE.md` end-to-end. Cross-reference
against the actual surfaces shipped in phases 4 / 7 / 8
(host CLI), the planner outputs from phases 2 / 6, and the
ABI surface from phase 1.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-rebase-commit-phase-NN-<descriptive>.md`. The master
plan is [PLAN-rebase-commit.md](/components/instar/plans/PLAN-rebase-commit/). This
phase is the twelfth and final of twelve.

I prefer one commit per logical step. The step table below
identifies three steps; this phase can land step by step or
as a single consolidated commit.

## Situation

After phase 11, every functional surface for `instar rebase`
and `instar commit` is shipped and tested:

- **ABI** (phase 1): `RebaseConfig`, `CommitConfig`,
  `RebaseResult`, `CommitResult`, `write_input_sector` call
  table primitive, `open_chain_devices_rw` helper.
- **Planners** (phases 2 + 6): `plan_rebase_qcow2`,
  `plan_rebase_vmdk` (unsafe + safe modes), `plan_commit_qcow2`,
  `plan_commit_vmdk` with per-format allocators.
- **Guest binaries** (phases 3 + 7): `rebase.bin`, `commit.bin`,
  with the two-device RW chain layout and the per-cluster
  data-comparison + cluster-allocation loops.
- **Host CLIs** (phases 4 + 8): `instar rebase`, `instar
  commit` with their clap surfaces, host pre-checks, KVM
  lifecycle wiring, and result harvesting.
- **Tests** (phases 5 + 9): smoke / matrix / round-trip
  layers for both subcommands, with cross-version baselines
  in `instar-testdata`.
- **Coverage-guided fuzzing** (phase 10):
  `fuzz_rebase_planners`, `fuzz_commit_planners` wired into
  the nightly CI run.
- **Differential fuzzing** (phase 11): `op_rebase`,
  `op_commit` in `scripts/differential-fuzz.py`.

What's missing is the **user-facing documentation surface**.
A new instar user opening `docs/` today finds per-subcommand
guides for measure / create / resize but nothing for rebase
or commit; the `CHANGELOG.md` `[Unreleased]` section
doesn't mention them; `README.md`'s Project Status line
still says "info, copy, check, compare, convert, measure,
create, and resize". Cross-cutting documents
(`docs/index.md`, `docs/format-coverage.md`,
`docs/quirks.md`, `ARCHITECTURE.md`) also need updates so
the new subcommands are reachable from the navigation
hierarchy.

The relevant existing infrastructure this phase builds on:

- **`docs/resize.md`** is the template for both new
  per-subcommand guides. Sections: Synopsis, End-spec /
  Mode grammar, Target formats, Output format, Known
  divergences from qemu-img, Future work, Examples. The
  same TOC works for rebase and commit with minor renaming.
- **`CHANGELOG.md`** uses the [Keep a Changelog] convention.
  The resize entry under `[Unreleased]` (line 12 onwards)
  is the most recent precedent and demonstrates the level
  of detail expected.
- **`docs/format-coverage.md`** has per-operation "Resize
  Format Support" subsections. Rebase + commit get
  matching "Rebase Format Support" and "Commit Format
  Support" subsections immediately after the existing
  Resize one.
- **`docs/quirks.md`** has a "resize subcommand quirks"
  section (line 996). The same pattern applies — rebase
  has known divergences (long-path relocation refusal,
  vmdk safe-mode unsupported); commit has known
  divergences (vmdk explicit-`-b` refusal, qcow2 scratch
  budget cap, no `-d` / `-p` / `-r` / `-t`).
- **`README.md` Project Status** line 30 — extend the
  operations list to `info, copy, check, compare,
  convert, measure, create, resize, rebase, commit`.
- **`README.md` Usage** section — add "Image Rebase" and
  "Image Commit" subsections after "Image Conversion",
  mirroring the per-subcommand subsection shape.
- **`docs/index.md` Operations table** (line 41) — add
  `[Rebase](/components/instar/plans/rebase/)` and `[Commit](/components/instar/plans/commit/)` rows.
- **`ARCHITECTURE.md`** — the new ABI surface from phase 1
  (two-device RW chain config, `write_input_sector`
  primitive, `send_*_result` call-table arms) is a small
  architectural addition.

## Mission and problem statement

After phase 12 lands:

1. **`docs/rebase.md`** exists. Covers:
   - Synopsis with the full `instar rebase` flag surface
     (`-u`, `-b`, `-F`, `-q`, `--output`).
   - Mode grammar (unsafe vs safe; detach via empty `-b`).
   - Target formats table (qcow2 unsafe + safe, vmdk
     monolithicSparse unsafe only).
   - Output format (human `"Image rebased." / "Image
     detached."` vs `--output=json` structured envelope).
   - Known divergences from `qemu-img rebase` (long-path
     relocation refusal, vmdk safe-mode unsupported, no
     `--object` / `--image-opts`, etc.).
   - Future work.
   - Examples.

2. **`docs/commit.md`** exists. Covers:
   - Synopsis with the full `instar commit` flag surface
     (`-f`, `-b`, `-q`, `--output`).
   - `-b` resolution semantics (implicit vs explicit;
     immediate-parent-only constraint).
   - Target formats table (qcow2 + vmdk monolithicSparse;
     vmdk implicit-`-b` blocked by the info-vmdk-
     backing-file follow-up).
   - Output format (human `"Image committed."` vs
     `--output=json` structured envelope per phase 8 open
     question 9).
   - Known divergences from `qemu-img commit` (no `-d`
     drop, no `-p` progress, no `-r` rate-limit, no `-t`
     cache, no intermediate-image commit, etc.).
   - Future work.
   - Examples.

3. **`README.md`** updated:
   - Project Status line extends to include rebase + commit.
   - "See [docs/...md] for the per-subcommand user
     guides" line points at the new files.
   - New "Image Rebase" and "Image Commit" subsections in
     Usage with three or four canonical command-line
     examples each.

4. **`CHANGELOG.md`** `[Unreleased]` section carries two
   new entries (one per subcommand), each in the same
   shape as the existing resize entry — surface
   description, per-format support table, known
   divergences, link to the per-subcommand docs, link to
   every phase plan that shipped.

5. **`ARCHITECTURE.md`** updated with the new ABI surface
   bullets: two-device RW chain config (overlay at input
   slot 0 RW for commit, output device opened RW),
   `write_input_sector` primitive (commit-only),
   `send_rebase_result` / `send_commit_result` call-table
   arms.

6. **`docs/index.md` Operations table** carries
   `[Rebase](/components/instar/plans/rebase/)` and `[Commit](/components/instar/plans/commit/)` rows.

7. **`docs/format-coverage.md`** carries "Rebase Format
   Support" and "Commit Format Support" subsections
   following the same shape as the existing "Resize Format
   Support" subsection (per-format support tables +
   instar-only vs cross-tool-diff coverage notes).

8. **`docs/quirks.md`** carries "rebase subcommand quirks"
   and "commit subcommand quirks" sections following the
   same shape as the existing "resize subcommand quirks"
   section.

9. `make instar` / `make lint` / `pre-commit run --all-files`
   are clean. `make test-rust` doesn't regress.

10. The execution-table row for phase 12 in
    `PLAN-rebase-commit.md` is marked Complete with the
    shipping commit hashes. **All twelve phases of
    PLAN-rebase-commit are complete.**

## Open questions

### 1. One commit per file, or grouped by step?

Working choice: **grouped by step**. The per-step
organisation matches phases 1–11. Step 12a is the heavy
content step (two new ~300-line user-guide files); 12b is
the cross-cutting updates (multiple shorter edits to
existing files); 12c is wrap-up.

### 2. Per-subcommand doc shape

Working choice — mirror `docs/resize.md` exactly. The
TOC adapts trivially:

**`docs/rebase.md`:**
1. Synopsis
2. Mode grammar (unsafe vs safe)
3. Target formats (qcow2 unsafe + safe, vmdk unsafe only)
4. Output format (human / JSON)
5. `-u` (unsafe) semantics
6. `-b` (new backing) / detach semantics
7. Known divergences from `qemu-img rebase`
8. Future work
9. Examples

**`docs/commit.md`:**
1. Synopsis
2. `-b` resolution (implicit vs explicit)
3. Target formats (qcow2; vmdk explicit-`-b` only)
4. Output format (human / JSON)
5. Atomicity guarantees (data into backing first, backing
   metadata next, overlay-clear pass last)
6. Known divergences from `qemu-img commit`
7. Future work
8. Examples

### 3. CHANGELOG entry depth

Working choice — match the resize entry's depth. Each new
entry covers:
- Surface description (one paragraph).
- Per-format support table.
- Two or three notable known divergences.
- Link to the per-subcommand doc.
- Links to every phase plan that shipped (parenthetical
  list of phase 1 through phase 12 plans).

### 4. `ARCHITECTURE.md` updates: how much?

Working choice — **minimal but sufficient**.
`ARCHITECTURE.md` today covers the security model, the
sandboxed-conversion-engine pattern, and the RAW
detection security feature. The phase 1 ABI additions are
a natural extension: one or two bullets noting the
two-device RW chain config and the `write_input_sector`
call-table primitive, with pointers to
`docs/chain-config.md` (already exists) for the layout
detail. No deep dive — that's what `docs/rebase.md` and
`docs/commit.md` are for.

### 5. `docs/usage.md` updates?

Working choice — **no updates**. `docs/usage.md` is a
downstream-tool catalog (Cinder / Nova / VDSM / Proxmox)
of qemu-img call patterns. The rebase + commit operations
are already listed in the per-tool inventories there
(line 12 of `docs/usage.md` references Proxmox's rebase
+ commit usage, etc.). Mentioning that instar now
implements them too would be useful but is not
load-bearing for phase 12 success criteria.

### 6. `docs/usage.md` "instar coverage matrix" update?

Working choice — **yes if it exists**. There's an
"instar coverage matrix" table around line 887 that
tracks per-operation support. The rebase + commit rows
already exist there (lines 887–888 from the earlier
grep); double-check they're marked supported now, since
they were marked TBD before phase 8.

### 7. Per-format format-coverage table cells

Working choice — match phase 9's matrix:

**Rebase Format Support:**

| Format | Unsafe mode | Safe mode | qemu-img parity |
|--------|-------------|-----------|------------------|
| qcow2  | Yes         | Yes       | Byte-equivalent across qemu-img 6.0.0–10.2.0 (modulo `KNOWN_REBASE_DIVERGENCES`) |
| vmdk monolithicSparse | Yes | Reject (planner gap) | **instar-only**: `qemu-img rebase` rejects vmdk |
| Other formats | Reject | Reject | **n/a** — both refuse |

**Commit Format Support:**

| Format | Status | qemu-img parity |
|--------|--------|------------------|
| qcow2  | Yes    | Byte-equivalent across qemu-img 6.0.0–10.2.0 (modulo `KNOWN_COMMIT_DIVERGENCES`) |
| vmdk monolithicSparse | Yes (explicit `-b` only) | Cross-version baselines recorded; implicit-`-b` blocked by info-vmdk-backing-file follow-up |
| Other formats | Reject | n/a |

### 8. Quirks sections shape

Working choice — mirror the resize quirks section:

**`rebase subcommand quirks`:**
- "Unsafe (`-u`) is byte-equivalent." Like resize, the
  unsafe-mode rebase post-image's `qemu-img info` matches
  qemu-img's byte-for-byte after path normalisation.
- "Safe-mode rebase for vmdk is not yet supported." instar
  rejects with a clear error; qemu-img also rejects (no
  upstream vmdk rebase at all).
- "Long-path relocation is rejected." Long new-backing
  paths that don't fit the overlay's existing header slot
  are refused with `ERROR_BACKING_PATH_TOO_LONG`. qemu-img
  silently relocates. Lifting the gap is master-plan TODO.
- (Continue for other documented gaps.)

**`commit subcommand quirks`:**
- "`-d` (drop overlay), `-p` (progress), `-r` (rate
  limit), `-t` (cache) not yet supported." instar accepts
  the long-form aliases on the surface but the
  semantics are deferred per the master plan.
- "Intermediate-image commit not yet supported." instar's
  host pre-check rejects `-b BASE` against a non-parent
  with a clear "use `qemu-img commit` for deep-chain"
  message.
- "vmdk implicit-`-b` blocked." The host pre-check refuses
  every vmdk commit without explicit `-b` because the
  host info operation doesn't currently expose vmdk
  monolithicSparse's `parentFileNameHint`. Tracked
  separately under PLAN-info's vmdk follow-ups.
- "Cluster-size mismatch refused." If overlay and backing
  have different qcow2 cluster sizes the host pre-check
  refuses with a clear message. Lifting is master-plan
  TODO.

### 9. `docs/plans/order.yml` updates

`PLAN-rebase-commit.md` is already in `order.yml` (line 12
of the file from the earlier grep). The phase plan files
themselves aren't indexed — the master plan's execution
table links to each phase. No `order.yml` change required.

## Execution

The phase plan recommends three steps. Each step is small
enough to review independently; consolidating into one or
two commits at the end is also fine.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 12a | high | sonnet | none | Write `docs/rebase.md` and `docs/commit.md` from scratch, mirroring `docs/resize.md`'s TOC and depth. Each file ~250–350 lines covering Synopsis, mode/`-b` grammar, target-format table, output format (human + JSON), command-specific semantics, known divergences from the qemu-img counterpart, future work, examples. Pull factual content from the phase-1 ABI definitions, phase-2/6 planner crates, phase-3/7 guest binaries, phase-4/8 host CLIs, phase-5/9 test matrices. `make lint` clean (the markdown linter checks heading levels + reference-style links). |
| 12b | medium | sonnet | none | Cross-cutting updates: `README.md` (Project Status line, "See …" pointers, new Image Rebase + Image Commit subsections in Usage), `docs/index.md` (Operations table rows), `docs/format-coverage.md` (Rebase + Commit Format Support subsections), `docs/quirks.md` (rebase + commit subcommand quirks sections), `ARCHITECTURE.md` (two-device RW chain + `write_input_sector` notes), `CHANGELOG.md` (two `[Unreleased]` entries mirroring the resize entry's depth). Cross-references between the new and existing docs use relative paths. |
| 12c | low | sonnet | none | Pre-commit clean. Master plan updated to mark phase 12 complete with shipping commit hashes. The "Future work created by this phase" section enumerates the open items still tracked. Note in the master plan's summary that all twelve phases are complete. |

## Agent guidance

### Execution model

Same model as phases 1–11: implementation work runs in the
management session unless explicitly delegated. The model
guidance in the step table is conservative (sonnet for
both content-heavy and cross-cutting steps); the management
session can stick with sonnet throughout — phase 12 is
documentation, not new logic.

### Planning effort

The master plan flagged this phase as **medium effort**.
Within the phase, 12a is high (two new ~300-line files);
12b is medium (many small edits across seven existing
files); 12c is low.

### Step ordering

Strict dependency: 12a → 12b → 12c. 12b can interleave
with 12a since they touch different files, but the natural
review order is 12a (per-subcommand docs — the load-
bearing content) then 12b (cross-cutting updates that
cross-reference the 12a files) then 12c (wrap-up).

### Management session review checklist

After each step:

- [ ] The files that were supposed to change actually
      changed.
- [ ] No unrelated files modified.
- [ ] `pre-commit run --all-files` clean (markdown linter,
      Rust formatters, etc.).
- [ ] Every relative link in the new docs resolves
      (`grep -E '\\]\\([^)]+\\)' docs/rebase.md docs/commit.md`
      sanity-check).
- [ ] The CHANGELOG entries are under `[Unreleased]`, not
      a versioned heading.
- [ ] No stale "TBD" or "TODO" markers left in the new
      docs.

## Administration and logistics

### Success criteria

Phase 12 is complete when:

* `docs/rebase.md` and `docs/commit.md` exist and cover
  the surface described in Mission points 1–2.
* `README.md` mentions both new subcommands in Project
  Status and Usage.
* `CHANGELOG.md` `[Unreleased]` carries two new entries
  matching the resize entry's shape.
* `ARCHITECTURE.md`, `docs/index.md`,
  `docs/format-coverage.md`, `docs/quirks.md` all carry
  the cross-cutting updates.
* `make instar`, `make lint`, `make test-rust`,
  `pre-commit run --all-files` are all clean.
* The execution-table row for phase 12 in
  `PLAN-rebase-commit.md` is marked Complete with the
  shipping commit hashes, and the master plan's preamble
  notes that all twelve phases are complete.

### Future work created by this phase

The new docs explicitly enumerate the deferred items
already tracked elsewhere in the codebase — this phase
doesn't create new follow-ups, it documents the existing
ones so they're discoverable from each subcommand's
landing page. The Future-work sections of `docs/rebase.md`
and `docs/commit.md` will cross-reference:

- Long-path relocation in qcow2 rebase (planner gap).
- Vmdk safe-mode rebase (planner gap).
- `-d` / `-p` / `-r` / `-t` in commit (CLI surface gaps).
- Intermediate-image commit (planner + chain-walking
  gap).
- Cross-format commit (planner gap).
- Implicit-`-b` for vmdk commit (info-side gap).
- Cluster-size-mismatch commit (planner gap).
- Cross-cluster-size rebase (planner gap).

### Bugs fixed during this work

None — phase 12 was pure documentation work. No code paths
were modified. The `pre-commit run --all-files` check ran
clean after both 12a and 12b.

### Documentation index maintenance

`PLAN-rebase-commit.md` is already in
`docs/plans/order.yml`; no `order.yml` change required.
The new per-subcommand docs (`docs/rebase.md`,
`docs/commit.md`) live at `docs/` not `docs/plans/` and
are not indexed by `order.yml`.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
