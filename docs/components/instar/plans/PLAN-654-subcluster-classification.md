# PLAN-654: One subcluster classification for qcow2

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

Consult `ARCHITECTURE.md` for the overall system structure
(host VMM, KVM guest, call table, device emulation).
Consult `AGENTS.md` for build commands, project conventions,
code organisation, and the security model summary. Consult
`docs/` for format-specific documentation (`docs/qcow2/`,
`docs/raw/`, etc.) and `docs/commentary/` for architectural
decisions and design rationale.

The request was "/pick-issue", which recommended issue #654 together
with #653, and then "start on the pick, with a plan first". Two
decisions were taken by the operator on 2026-10-19 after the survey
below: invalid entries are refused for qemu parity (D2), and the work
is one plan in two pull requests, with the data-device defect the
survey turned up filed as #792 and made phase 2.

<!-- shared-block: plan-file-conventions v2 -->
Plan file conventions (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-file-conventions.md`):

- All planning documents live in `docs/plans/`.
- Detailed planning gets one plan file per phase. Phase files are
  named for their master plan, sit in the same directory as it,
  and append `-phase-NN-descriptive` before the `.md` extension.
- The master plan tracks its phases in a table under its Execution
  section. `Merged` is last, and the push audit is the last row,
  for the reasons given in `plan-push-audit-phase`:

  | Phase | Plan | Status | Merged |
  |-------|------|--------|--------|
  | 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started | |
  | 2. Public API | PLAN-thing-phase-02-api.md | Not started | |
  | 3. Push audit | - | Not started | |

- One commit per logical change, and at minimum one commit per
  phase. Unrelated changes are not batched into a single commit.
  Each commit is self-contained: it builds, passes tests, and has
  a message explaining what changed and why.
<!-- shared-block-end -->

## Situation

An extended-L2 qcow2 entry is a 64-bit L2 word plus a 64-bit subcluster
bitmap (bits 0-31 allocated, bits 32-63 reads-as-zero). What a
subcluster means is a function of three things together: the L2 word's
host offset, the two bitmap bits for that subcluster, and whether the
image has an external data file. qemu computes it in exactly one place,
`qcow2_get_subcluster_type` (`block/qcow2.h:747-780` at v10.0.0), on
top of `qcow2_get_cluster_type` (`:713-737`), and yields one of seven
types: `NORMAL`, `ZERO_ALLOC`, `ZERO_PLAIN`, `UNALLOCATED_ALLOC`,
`UNALLOCATED_PLAIN`, `COMPRESSED` and `INVALID`. An `INVALID` entry
fails the read with `EIO` and signals corruption
(`block/qcow2-cluster.c:1197-1205` and `:712-718`).

instar has no such function. Each consumer decodes the entry itself,
and each treats a host offset of zero ad hoc:

| Consumer | Where | What it does with host offset 0 |
|---|---|---|
| `Qcow2State::cluster_lookup` | `src/crates/qcow2/src/lib.rs:2283-2322` | classic: `Unallocated`; extended, all alloc: `Standard(0)`; extended otherwise: `StandardSubclusters(0, bitmap)` |
| chain reader, narrow arm | `lib.rs:13309-13442` | `!is_alloc` zero-fills instead of descending; `is_alloc` zero-fills "defensively" |
| chain reader, wide arm | `lib.rs:13443-13543` | the same two choices, written separately |
| chain reader, `Standard` arm | `lib.rs:13546-13601` | reads `0 + intra_offset`: the image's own header |
| map | `classify_qcow2_l2_standard` `:1754`, `classify_qcow2_l2_extended` `:1826` | hole |
| measure | `count_allocated_in_l2_extended` `:1675` | not counted |
| check | `validate_subcluster_bitmap` `:1442`, called from `src/operations/check/src/main.rs:2867,2907` | `AllocWithoutHost` error |

None of them knows qemu's external-data-file rule: with a data file,
offset 0 is a real offset, and an entry of `QCOW_OFLAG_COPIED | 0` is
`NORMAL` (`qcow2.h:724-733`). qemu maps data-file clusters one to one,
so virtual cluster 0 of every data-file image is written that way.

### Measured

All against qemu-img 10.0.13 and instar built from develop @
`da9f043f` on 2026-10-19. The two crate-level measurements are from
the issues.

| # | Image | instar | qemu |
|---|---|---|---|
| A (#654) | extended L2, backing file, an unallocated cluster with some zero bits (host 0, alloc 0) | subclusters with neither bit set read zeros | read the backing file |
| B (#653) | extended L2, no data file, `l2 = COPIED`, all alloc bits set | serves file offset 0 (header, L1) as guest data | `EIO`, "Invalid cluster entry found" |
| C | extended L2, data file, `write -P 0xab 0 4k` (entry `0x8000000000000000`, bitmap `0x3`) | `convert` writes 4096 zero bytes where the data is; `check` reports a bitmap error and exits 2; `map` reports cluster 0 as a hole; `measure` is one cluster short (458752 against 524288) | data read; `check` clean; `map` data at offset 0 |
| D | classic L2, data file, cluster 0 written | `map` reports cluster 0 as a hole; `convert` is right, by accident (see #792) | data at offset 0 |

Every row exits 0 except C's `check`. Rows C and D are images qemu
writes, not corrupt ones, which contradicts the claim in
`docs/quirks.md:4865` ("Only genuinely corrupt or hostile images
contain this shape; no qcow2 writer produces it").

### How this was found, and why the tests miss it

This is the fourth defect in the same branch of the same function.
#650 was found by `fuzz_chain_compose`, and #653 and #654 were found
while reviewing its fix in #652. Each was patched by adding a
condition, which is how the next one was missed. The crate tests for
#650 use `chain_len` 1, so there is no backing device to descend into.
`fuzz_chain_compose`'s invariant 4 compares only non-zero bytes,
because from outside the reader a hole cannot be told apart from a
zero. The comment on #654 measures this: a version of the invariant
that tries to infer ownership compared **zero holes in 132,908
executions**. The test fixtures in the suite have no external data
file with extended L2.

Row D's convert result is right only because the VMM puts the data
device in the chain directly after its qcow2 (`src/vmm/src/chain.rs:166-170`),
so an `Unallocated` cluster falls into it and reads the data file at
the virtual offset. That same fall-through is wrong for genuinely
unallocated clusters: they read the data file instead of the backing
chain, or stale data-file bytes instead of zeros. That is #792, filed
from this survey, and it is phase 2 here because phase 1 is what makes
it fixable (see D4).

## Mission and problem statement

Establish one invariant and test it by construction:

> **Every consumer that interprets a qcow2 L2 entry gets its subcluster
> type from one function, which implements `qcow2_get_subcluster_type`
> including the external-data-file rule. No consumer gives host offset
> zero any meaning beyond what that type says.**

The consumers are the chain reader's arms, map, measure and check. The
function lives in `src/crates/qcow2`, is `no_std`, and is pure, so it
is unit-tested against qemu's table rather than through a guest.

## Decisions

**D1. Mirror qemu's classification exactly.** The function takes the
L2 word, the bitmap, the subcluster index, `extended_l2` and
`has_data_file`, and returns one of the seven types above. Classic
(non-extended) entries go through it too, using qemu's non-subcluster
mapping (`qcow2.h:781-796`), so there is one decision tree rather than
two. A range variant in the shape of `qcow2_get_subcluster_range_type`
(`qcow2-cluster.c:395`) returns the type and the run length, which is
what the reader's run coalescing and map's extent coalescing both
need. Its tests are a table transcribed from qemu's source with the
line cited, plus the measurements from step 1a.

**D2. `INVALID` refuses: qemu parity.** Decided by the operator on
2026-10-19. The read fails, as qemu's does with `EIO`, for every shape
qemu calls `INVALID`:

* host offset 0 with any alloc bit, and no data file (#650 and #653);
* an alloc bit and a zero bit both set for one subcluster, at any host
  offset.

This is a behaviour change on images accepted today. It retires the
zero-fill policy of #650 and the quirk entry
`docs/quirks.md:4848` ("Malformed qcow2 subcluster: instar zero-fills
where qemu-img errors"), whose premise row C disproves. The VHD and
VHDX sector-bitmap readers already refuse their equivalent
contradictions, so qcow2 becomes consistent with them. A compressed
entry with a non-zero bitmap stays readable: qemu returns `COMPRESSED`
without looking at the bitmap, and `check` remains the place that
flags it.

**D3. The data-file rule lives in the classifier, not in its callers.**
`has_data_file` comes from `Qcow2State::incompatible_features &
INCOMPAT_EXTERNAL_DATA`, which the state already carries. With it,
`COPIED | 0` is `NORMAL` at host offset 0 and is read from
`data_device_idx`, as the existing `Standard` arm already does for
non-zero offsets (`lib.rs:13557-13565`). That fixes row C, and fixes
map and measure for rows C and D.

**D4. Phase 2 cannot go first.** Making `Unallocated` skip the data
device (#792) without D3 would break virtual cluster 0 of every
data-file image. With classic L2 that cluster is `COPIED | 0`, which
instar calls `Unallocated` today and reads correctly only because it
falls into the data device. After phase 1 that cluster is `NORMAL` and
is read for the right reason, and phase 2 can remove the fall-through.

**D5. `check` reports what the classifier calls `INVALID`, and
otherwise follows `qemu-img check`.** Its `HostWithoutRef` rule (host
offset non-zero, bitmap zero) is not a qemu `INVALID`; qemu reads such
an entry as 32 `UNALLOCATED_ALLOC` subclusters. Whether `qemu-img check`
nonetheless reports it is measured in step 1a, not assumed, and `check`
follows the measurement.

**D6. Tests get ownership by construction.** A fixture matrix that
knows which subclusters the child owns and what type each is, over a
backing device whose content is known and non-zero, asserting per type
per arm that every byte comes from the right place. "Arm" means narrow
(sector size at most the subcluster size) and wide (sector size larger)
paths, both of which exist because 512- and 4096-byte sectors meet 2K
to 64K subclusters. Any fuzz invariant that comes out of this carries a
reachability probe, so it cannot pass by never doing any work, which is
the failure #654 measured.

## Open questions

1. **How the refusal is reported.** The reader returns `bool` to about
   ten call sites across convert, compare, bench and rebase, and each
   turns `false` into a generic read failure. The precedent for a
   named refusal is `send_differencing_refusal` (`lib.rs:15160`), which
   sends a structured error that the VMM renders. Step 1c decides
   whether `INVALID` gets the same treatment. The error-handling skill
   argues it should, since the user should be able to tell a corrupt
   image from an I/O fault. #665 is a reminder that a raiser needs a
   matching renderer.
2. **Write paths.** snapshot, commit, rebase, resize, bench `-w` and
   `qcow2-write` also read L2 entries. `docs/quirks.md:2208` says write
   operations refuse external data files. Step 1a lists every write
   path that can meet an extended-L2 entry and says whether any of them
   interprets host offset 0. Paths that refuse data files and only
   meet entries they have already validated are out of scope, and the
   plan records which ones they are.
3. **A silent bitmap read failure.** `cluster_lookup` reads the bitmap
   with `.unwrap_or(0)` (`lib.rs:2279`), so a failed read becomes
   "nothing allocated" and the cluster reads from the backing file or
   as zeros. That is the same kind of quiet wrong answer, in the
   function this phase rewrites, so phase 1 propagates the failure
   rather than leaving it.

## Execution

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. One subcluster classifier (#653, #654, rows C and D) | [PLAN-654-subcluster-classification-phase-01-classifier.md](/components/instar/plans/PLAN-654-subcluster-classification-phase-01-classifier/) | In progress | |
| 2. The data device is not a chain layer (#792) | PLAN-654-subcluster-classification-phase-02-data-device.md | Not started | |
| 3. Push audit | - | Not started | |

Phase 2's plan is written by `/next-phase` once phase 1 has merged,
because its premises depend on how phase 1 lands.

<!-- shared-block: plan-push-audit-phase v3 -->
Push audit phase (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-push-audit-phase.md`):

- Every master plan ends with a phase that runs the repository's
  `PUSH-AUDIT.md` over the whole plan's work. It is the last row of
  the Execution table and it is not optional. The rule binds every
  plan that carries the phase, which is decidable from the plan file
  alone: a plan that is already `Complete`, `Abandoned` or
  `Superseded` and does not carry the phase is not reopened to
  acquire one, and a plan that has the phase runs it even if it
  reaches `Complete` before the phase does.
- That phase audits the accumulated diff of every phase in the plan
  against the default branch, not the diff of the last phase alone.
  Auditing one phase at a time would miss what the phases did to
  each other -- the duplicated helper that only exists once phases
  three and six have both landed, the doc page that phase two made
  wrong and phase five never revisited.
- Once the plan's phases have merged, a diff against the default
  branch is empty and would read as a clean audit. The range is not
  reliably derivable after the fact either: unrelated work lands on
  the default branch between phases, so anything anchored on "since
  the plan file appeared" is far too wide. It has to be recorded. As
  each phase lands, what put it on the default branch goes into the
  plan: the merge commit of its pull request, whose diff against its
  first parent is the whole of what landed, or -- where the phase
  landed directly -- every commit of the phase, or its `first..last`
  range. A single commit is only ever enough when it is a merge
  commit.
- Where the Execution phases are a table, that record is a `Merged`
  column, added last so that a row which omits it still reaches
  `Status`; where they are prose sections it is a `Merged:` line in
  the phase's own section. The `Status` column keeps its single
  vocabulary term and nothing else (see `plan-status-vocabulary`).
  A phase that landed in another repository records `<repo> <sha>
  (#pr)` and is audited against that repository's default branch, as
  part of the pull request that lands it; the plan's own push-audit
  phase cites that audit rather than re-running it.
- Phases that landed before the plan started recording them are
  reconstructed rather than left blank. Recover what you can from
  `gh pr list --state merged` and `git rev-list --first-parent`, and
  say in the plan that the range was reconstructed. Do not trust a
  path-filtered `git log` on its own: it lists the commits that
  touched a path without saying which arrived directly and which
  arrived inside a pull request, and recording a commit that came in
  under a merge audits one commit of that pull request rather than
  the pull request. A reconstructed record may be a summary table in
  the audit phase's own section rather than a column or a line in
  the Execution table, which keeps retrospective archaeology out of
  a table that tracks live status. Where a phase accreted over
  months of unrelated commits and no range is recoverable, say that
  instead and name the paths the audit read -- an audit that says
  what it could not scope is a result; one that silently audits
  nothing is not.
- Findings land as their own pull request against the default
  branch, and the plan is not complete until they are resolved or
  explicitly declined in writing. A finding that is declined says
  why, in the plan, where the next reader will find it.
- Where the audit finds nothing, record that in the plan in one
  sentence. It is a real result, and a run of them is the evidence
  for making the phase conditional rather than mandatory.
- A repository with no `PUSH-AUDIT.md` still carries the phase, and
  the phase says that the runbook does not exist yet and what was
  done instead. Silently omitting it is what let the audit go
  untriggered for as long as it did.
<!-- shared-block-end -->

!!! note "In this project"

    The Execution phases are the `Phase | Plan | Status` table each
    master plan carries, so the record goes in a `Merged` column
    added after `Status` — last, so that a row which omits it still
    reaches `Status`. Most phases here land as their own pull
    request and the cell holds that one merge commit; a phase that
    landed directly on `develop` records its `first..last` range
    instead. The audit phase itself runs `PUSH-AUDIT.md` over the
    union of those ranges rather than over `git diff develop...HEAD`,
    which is empty once the phases have merged.

    The obligation starts with plans written from here on, and with
    any plan still open that carries an audit phase. Plans that were
    already `Complete` when the rule arrived are not being backfilled,
    so a `Phase | Plan | Status` table with no `Merged` column is a
    plan that predates this, not a plan that has drifted from it.

<!-- shared-block: plan-phase-landing v1 -->
Phase landing (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-phase-landing.md`):

A plan's status and a repository's review state both live in files
that every branch would otherwise rewrite. Left alone, that turns
each of them into a merge-conflict hot spot, and it spends a pull
request and a full CI run on a change that is entirely prose.
Three rules keep them out of the way.

- **A phase is closed out in the first commit of the next phase,
  not in a pull request of its own.** By the time the next phase
  branches, the previous one has merged, so its merge commit is
  known and its `Merged` cell can record the thing the push-audit
  phase actually needs. This is the only ordering that works: a
  phase cannot record its own merge commit, and a separate
  close-out pull request buys that record at the price of a round
  trip. The close-out sets the finished phase's `Status` and
  `Merged` cells and the plan's row in `docs/plans/index.md`, and
  it is committed before the next phase's own work, so that the
  branch never claims the plan is further along than the default
  branch is.

- **The last phase closes itself out.** The push-audit phase is
  the last row of every plan, so no next phase will carry its
  close-out. Where the audit raises findings, the plan is not
  complete until they are resolved or declined, and those land as
  their own pull request after the audit phase has merged -- so
  that pull request is the carrier, and it can record the audit
  phase's merge commit, which by then is known. Where the audit
  finds nothing there is no carrier, and no follow-up pull
  request is opened for the sake of one cell: the phase sets its
  own `Status`, and the plan's index row, to `Complete` in its
  own pull request, and records no `Merged` cell. It is the only
  row permitted to omit one. The column exists so that the
  push-audit phase can reconstruct what to audit; the audit phase
  is last, so nothing ever reads its own row.

- **`REVIEWS.md` is not pruned or regenerated in a pull request
  that changes code or documentation.** Editing a reviewed file
  stales its mark, and adding or removing an in-scope file moves
  the header count, but neither is the landing pull request's
  business. `prune` regenerates the file whether or not it dropped
  anything, so the `prune-reviews` workflow heals both on the next
  push to the default branch. Pruning from a branch is also wrong
  more often than it is right, though not for the reason it first
  appears: `prune` compares each stamp against `HEAD`, which on a
  branch is the branch tip, so it drops the marks for the files the
  pull request itself touched while keeping marks the default
  branch has already pruned. Committing that state merges a review
  file computed from a stale tree, and can resurrect marks
  `prune-reviews` has already removed. Accumulated staleness is
  reported by the `review-coverage` audit, which recomputes
  coverage against `HEAD` and raises an issue once the backlog is
  worth a review session.

  **A review session is the exception**, and it is not optional
  tidiness: `stamp` regenerates `REVIEWS.md` as well as writing the
  marks, and the rows, the sidecars and the marks are committed
  together (see `docs/code-review-tracking.md`). Where a repository
  requires a pull request to reach its default branch, that is how
  a review session lands, so "not in a pull request" is about the
  kind of change, not the mechanism.

These rules assume phases land one after another. Where two phase
branches are open at once, each closes out only the phase it
directly follows.
<!-- shared-block-end -->

!!! note "In this project"

    instar does not track per-file human review: there is no
    `REVIEWS.md`, no `prune-reviews` workflow and no
    `docs/code-review-tracking.md`, so the third rule has nothing to
    act on here until that tracking arrives. The first two rules
    apply as written to the `Phase | Plan | Status | Merged` table
    each master plan carries: the next phase's first commit sets the
    finished phase's `Status` and `Merged` cells and its row in
    `docs/plans/index.md`, and the push-audit phase closes itself out.

<!-- shared-block: plan-status-vocabulary v1 -->
Plan status vocabulary (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-status-vocabulary.md`):

A status cell -- in the master plan's own Execution phase table, and
in the row `docs/plans/index.md` carries for the plan -- holds
exactly one of these terms and nothing else:

- `Proposed` -- written down as a concept, not yet scheduled.
- `Not started` -- scheduled, but no work has begun.
- `In progress` -- work has begun and has not finished.
- `Blocked` -- cannot proceed until something outside the plan
  changes. Say what, in the plan.
- `Complete` -- the work is done.
- `Abandoned` -- deliberately dropped without being done.
- `Superseded` -- replaced by another plan, which the plan names.

The term is the whole cell. No dates, no phase arithmetic, no
parenthetical qualifiers, no summary of what happened: a status is
read to decide whether a plan still wants attention, and prose in
that column has repeatedly grown until it could no longer be read
either by a person scanning the table or by tooling. Detail belongs
in the plan file, and a one-line summary belongs in the index's own
Intent column.

Matching is case-insensitive, so `In Progress` is accepted, but the
spelling above is the one to write.
<!-- shared-block-end -->

## Agent guidance

### Execution model

<!-- shared-block: subagent-execution-model v1 -->
Sub-agent execution model (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-execution-model.md`):

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making. This keeps the management
context lean and avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with the
   brief from the plan, at the recommended effort level and model.
3. **Review** the sub-agent's output in the management session.
   Check the actual files -- the sub-agent's summary describes
   what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether the
   brief was insufficient (improve it) or the model was too light
   (upgrade it), then re-run.
5. **Commit** once the management session is satisfied.

This applies to all steps, including high-effort ones. If a
sub-agent cannot succeed even with a detailed brief and the right
model, that is a signal the brief needs improving, not that the
management session should do the implementation itself.

Use `isolation: "worktree"` for sub-agents when the change is
risky or experimental; the worktree is discarded if the output is
unsatisfactory. For safe, well-understood changes, sub-agents can
work directly in the main tree.
<!-- shared-block-end -->

### Planning effort

<!-- shared-block: plan-planning-effort v1 -->
Planning effort (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-planning-effort.md`):

The master plan itself is always created at **high effort** -- it
requires broad codebase understanding, cross-referencing several
source files, and judgment calls about scope and sequencing.

Each phase plan states the recommended effort level for planning
that phase. Phases that turn on design decisions, cross-component
coordination, protocol changes, or subtle correctness questions
should be planned at high effort. Phases that are mechanical, or
that follow a pattern already established elsewhere in the
codebase, can be planned at medium effort.
<!-- shared-block-end -->

!!! note "In this project"

    Phases involving deep protocol research, format-spec
    interpretation, or architectural decisions (call-table
    changes, new operations, new shared crates, security
    boundary changes) should be planned at high effort. Phases
    that are mechanical or follow well-established patterns can
    be planned at medium effort.

### Step-level guidance

<!-- shared-block: subagent-step-guidance v1 -->
Sub-agent step guidance (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-step-guidance.md`):

Each phase plan includes a table like this:

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | One-sentence summary of what to do and which files to touch |
| 1b | high | opus | worktree | Why this needs high effort: requires understanding X to do Y |

**Effort levels**, from cheapest to most thorough:

- **low** -- Purely mechanical changes: rename, reformat, add a
  log line, regenerate generated code. The brief is a complete
  instruction.
- **medium** -- The plan provides enough context to follow a clear
  brief. The sub-agent may read a few files, but the approach is
  already decided.
- **high** -- Requires reading several files, making judgment
  calls, or understanding non-obvious invariants. The sub-agent
  needs to think about edge cases.
- **xhigh** -- The setting for hard coding and agentic steps:
  long-horizon changes, or steps where the sub-agent must both
  research and implement.
- **max** -- Correctness matters more than cost. Expect
  diminishing returns and occasional overthinking; reserve it for
  steps where a wrong answer would be expensive to detect.

**Brief for sub-agent:** this is the key field. Write it as if
briefing a colleague who has never seen the codebase. Include what
to change, which files to touch, what patterns to follow, and any
non-obvious constraints.

A good brief front-loads the research the planner already did, so
the implementing agent does not repeat it. Instead of "add storage
functions for the new object", name the functions to add, the file
they belong in, the existing equivalent to mirror (with line
numbers), and any registration the change also needs.

The better the brief, the lower the effort level needed and the
lighter the model that can succeed.
<!-- shared-block-end -->

!!! note "In this project"

    The external references that push a step to high effort are
    format specs, the qemu-img source, and the KVM/virtio docs.
    The non-obvious constraints a brief should always state are
    memory layout, the 768KB guest binary cap, the `no_std`
    requirement of the format crates, and the call table
    boundary.

    Format-conversion work is the usual reason to need a
    large-context model: it tends to span the source format
    parser, the destination format writer, the call table, and
    the host-side glue at the same time.

    A worked brief for this codebase: instead of "add tests for
    the QCOW2 L2 parser", write "add tests for
    `parse_l2_entry()` in `src/crates/qcow2/src/lib.rs`. Use the
    adversarial fixtures in
    `instar-testdata/adversarial/qcow2/` (cluster boundary
    edges, OFLAG_COMPRESSED set with extended L2 cluster,
    refcount underflow). The function takes `(entry: u64,
    cluster_bits: u32)` and returns `Option<L2Entry>`."

### Model choice

<!-- shared-block: subagent-model-roster v1 -->
Sub-agent model roster (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/subagent-model-roster.md`):

The planner recommends which model is best suited to each step.
This is a judgment call, not a rigid rule -- the right model
depends on what the step requires, not on whether it is "planning"
or "implementation". The models available to sub-agents are:

- **fable** -- The most capable model available, for the hardest
  reasoning and the longest-horizon work: multi-step changes a
  single sub-agent must carry end to end, or steps whose
  correctness depends on holding a whole subsystem in mind at
  once. It costs materially more than opus, so reserve it for
  steps that have already defeated opus or are expected to.
- **opus** -- The default for steps needing deep reasoning,
  architectural understanding, subtle correctness judgment
  (locking, state machines, migrations), or intricate
  implementation that would be costly to debug if it were wrong.
- **sonnet** -- A good default for well-briefed implementation
  work. Faster and cheaper than opus, and effective when the plan
  front-loads the research and the brief leaves no broad judgment
  calls to make.
- **haiku** -- Suitable for purely mechanical tasks:
  search-and-replace, regenerating generated code, adding log
  lines, running commands. The brief must be a near-complete
  instruction.

Model choice interacts with effort level and brief quality. A
detailed brief compensates for a lighter model -- sonnet at medium
effort with a thorough brief often matches opus at medium effort
with a vague brief. The planner's job is to write briefs good
enough that the recommended model can succeed.

The model also determines the context window: fable, opus and
sonnet have 1M tokens, haiku has 200K. A step that must hold many
files in context at once may need one of the larger-context models
for that reason alone, even when the reasoning itself is
straightforward.

**When in doubt, skew to the more capable model.** Saving money
only matters if the outcome is still acceptable. A failed or
low-quality implementation wastes more time -- and therefore more
money -- than the heavier model would have cost. Recommend a
lighter model only when you are confident the brief is detailed
enough for it to succeed.
<!-- shared-block-end -->

### Management session review checklist

<!-- shared-block: plan-review-checklist v1 -->
Management session review checklist (shared block; do not edit --
the canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-review-checklist.md`):

After a sub-agent completes, the management session verifies:

- [ ] The files that were supposed to change actually changed --
      read them, do not trust the summary.
- [ ] No unrelated files were modified.
- [ ] The changes match the intent of the brief: not merely
      syntactically correct, but semantically right.
- [ ] The project's own pre-merge checks pass, including any
      generated code that has to be regenerated and committed
      (see the project-specific checks below).
- [ ] The commit message follows project conventions, including
      the `Co-Authored-By` line recording model, context window,
      and effort level.
<!-- shared-block-end -->

!!! note "In this project"

    The project-specific checks referred to above are:

    - [ ] `make instar` builds and `make lint` is clean.
    - [ ] Guest binaries pass `make check-binary-sizes` (768KB
          limit per operation).
    - [ ] `make test-rust` and the relevant
          `make test-integration` targets pass.
    - [ ] `pre-commit run --all-files` passes.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* `make instar` builds and `make lint` is clean.
* Guest binaries pass `make check-binary-sizes` (768KB limit).
* All Rust unit tests pass (`make test-rust`).
* All Python integration tests pass (`make test-integration`).
* `pre-commit run --all-files` passes.
* New format parsing is extracted into a shared crate under
  `src/crates/` where appropriate, and remains `no_std`
  compatible for guest use.
* Cross-validation against `qemu-img` is included for any new
  format or operation support.
* Documentation in `docs/` has been updated to describe the new
  features.
* `ARCHITECTURE.md`, `README.md`, `AGENTS.md`, and
  `CHANGELOG.md` have been updated as needed.

### Documentation index maintenance

When creating a new master plan from this template, update
the following files in `docs/plans/`:

* **`index.md`** — add a row to the *Master plans* table
  with the creation date, a link to the plan, a one-line
  intent summary, the initial status, and links to each
  phase plan file. Keep the table in chronological order.
* **`order.yml`** — add an entry for the new master plan
  so it appears in the documentation navigation bar. Phase
  files should *not* be added to `order.yml`.

When all phases of a plan are complete, update the status
column in `index.md` to *Complete*.

<!-- shared-block: plan-closeout-sections v1 -->
Plan close-out sections (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-closeout-sections.md`):

### Future work

We should list obvious extensions, known issues, unrelated bugs we
encountered, and anything else we should one day do but have
chosen to defer to here, so that we do not forget them.

* **Fuzzing by construction.** `fuzz_chain_compose` cannot see any of
  these defects (see Situation). A variant that drives images with
  ownership known by construction, as phase 1's fixtures have, with a
  reachability probe so it cannot pass vacuously, is the follow-up
  #654's comment argues for. It also touches the three fuzz-target
  lists that #651 says nothing cross-checks.
* **check's allocation count on data-file images.** On row C's image
  `instar check` reports `3/4097` clusters allocated where
  `qemu-img check` reports `3/16`. That is unrelated to classification,
  so it is not fixed here.
* **qemu's data-file offset check.** qemu refuses a data-file entry
  whose host offset differs from its guest offset
  (`block/qcow2-cluster.c:696-704`). instar does not check this. Phase
  2 should decide whether it belongs with #792.

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed. You should also scan the project's issue tracker,
where one exists, for directly related issues that we should
either resolve as part of this master plan or at least be aware of
while planning it.

Phase 1 is planned to close #653 and #654, and to fix rows C and D of
Situation for read, map, measure and check. Phase 2 is planned to close
#792. #650, fixed in #652, is related: D2 reverses its zero-fill policy.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->
