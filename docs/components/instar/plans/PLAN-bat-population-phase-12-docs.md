# Phase 12: cross-cutting documentation

Phase 12 of [PLAN-bat-population.md](/components/instar/plans/PLAN-bat-population/). Read its
phase 12 paragraph and the "Constraints that apply throughout" section,
in particular the rule that every qemu-img behaviour claim is either
cited to `instar-testdata/qemu-sources/` or measured with the static
builds, and the rule that code and user documentation do not cite this
plan's phase, step or decision numbers.

This phase writes no code. It carries the documentation that belongs to
no single phase, after phases 1 to 11 each updated the pages their own
change touched.

Planning effort: medium. The master plan allows it, and the survey
agrees: there are no design decisions here, the shape is a set of
factual corrections to five named pages. The one part that is not
mechanical is the pair of quirks entries, which assert what qemu-img
does and so must be measured before they are written rather than
reasoned about.

Review effort: medium, with one exception. A wrong claim in
`docs/quirks.md` is expensive: it is the page the next person trusts
when deciding whether a divergence is known, so a divergence described
backwards there is worse than one left undocumented. The review that
matters is of the two new quirks entries against their transcripts, not
of the roster edits, which are checkable by grep.

## What the survey found

The master plan's phase 12 paragraph was written before phases 1 to 11
executed. It names five targets; the survey found one of them needs
nothing, one needs a different edit from the one described, and one of
the three quirks it lists is already written. File and line references
are against `41f74d40`.

### The per-phase constraint held, and that is the finding

The master plan requires every phase that changes user-visible
behaviour to update the documentation describing it in the same pull
request, with phase 12 for the cross-cutting pages only. That held.
Counting mentions of the fixed subformat:

| Page | Owner | `subformat=fixed` mentions |
|------|-------|---------------------------|
| `docs/create.md` | phase 7 | 7 |
| `docs/convert.md` | phase 8 | 3 |
| `docs/measure.md` | phase 9 | 3 |
| `docs/quirks.md` | phases 7-9 | 6 |
| `docs/format-coverage.md` | **phase 12** | **0** |
| `docs/format-internals.md` | **phase 12** | **0** |

`docs/create.md` mentions `preallocation` 20 times; the output side of
`docs/format-coverage.md` mentions it zero times, its only occurrence
being in the resize table. The staleness is exactly where the master
plan predicted it would be, which is a real result and worth stating:
phase 12 is a documentation phase with a genuine, bounded backlog
rather than a sweep looking for work.

### `docs/format-internals.md` claims dynamic-only output and a flat 32 MiB block size

Its `vhdx` section opens:

```
Microsoft VHDX Virtual Hard Disk v2 (Hyper-V). Supported:
- Dynamic VHDX: BAT-based block allocation with 32 MiB blocks (input,
  output, check)
```

Both halves are now false:

* **Fixed output is missing.** `create -f vhdx -o subformat=fixed`
  landed in phase 7 and `convert -O vhdx --subformat fixed` in phase 8,
  and `measure` answers for a fixed target since phase 9. The word
  "fixed" does not appear in the file.
* **"32 MiB blocks" is right only for one size band.**
  `vhdx::default_block_size` in `src/crates/vhdx/src/lib.rs` returns
  64 MiB above 32 TiB, 32 MiB above 100 GiB, 16 MiB above 1 GiB and
  8 MiB at or below 1 GiB. 32 MiB is the answer for the 100 GiB to
  32 TiB band only. `docs/convert.md` already states the scaled rule
  correctly, so the two pages disagree with each other today.

The rest of the section -- the CRC-32C, metadata, overlap-rule and
region-cap prose -- is current and is not to be rewritten.

### `docs/format-coverage.md` names no VHDX subformat on the output side

Two places:

* The "Conversion Output Format Support" table's `vhdx` row says
  "Dynamic VHDX, configurable block size (1MB-256MB ...)". Its block
  size clause is correct, including the scaled default; its subformat
  claim is not.
* The "Output side" prose under the parity tables names vmdk's three
  subformats ("monolithicSparse, streamOptimized and monolithicFlat")
  but names no VHDX subformat, and says nothing about the no-op
  `preallocation` accepted on vmdk, vpc and vhdx since phase 3.

The `vhdx | ✓` cell in the create/convert-output/dd-output roster table
is still accurate and does not need a subformat column.

### `ARCHITECTURE.md`'s creatable roster omits monolithicFlat and fixed

```
Creatable target formats are raw, qcow2, vmdk (monolithicSparse and
streamOptimized), vpc (dynamic and fixed) and vhdx (dynamic).
```

Two omissions: `vmdk` gained host-only `monolithicFlat` in phase 3, and
`vhdx` gained `fixed` in phase 7. Note that `vpc` already reads
"(dynamic and fixed)", so the sentence's own form shows what the vhdx
entry should look like.

The master plan frames this target as "`ARCHITECTURE.md` if the shared
layout function changes the component description". That trigger does
not fire: the phase 5 shared layout function did not change how the
component is described. What is stale is a factual roster, which is a
smaller and different edit. `ARCHITECTURE.md` is loaded into every
session, so this phase corrects those two entries and adds nothing.

### `docs/quirks.md` is missing two divergences and already has the third

The master plan lists three for this phase. Measured against the tree:

| Divergence | State |
|---|---|
| convert's in-place fixed layout vs qemu's default | **already written** |
| `-o log_size` | missing |
| dynamic `block_state_zero` | missing |

The first is covered: `docs/convert.md` states the fixed output is the
layout of `qemu-img create -o subformat=fixed,block_state_zero=off` and
points at the "VHDX partial last block" section of `docs/quirks.md`,
which exists and discusses the `block_state_zero` layouts at those
sizes. Phase 8 did this work. Nothing to add.

The other two are real and undocumented:

* **`log_size` is accept-ignored.** `parse_create_o_options` (create)
  and `parse_o_options` (measure) in `src/vmm/src/main.rs` both match
  `("vhdx", "log_size")` to a bare accept-ignore arm, with the comment
  "log_size stays accept-ignored". instar writes qemu's default log
  size whatever the option says. The only occurrence of `log_size` in
  `docs/quirks.md` today is inside a pasted `qemu-img create`
  transcript, which is not a documented divergence. `convert` does not
  parse `-o` at all -- it takes `--subformat` -- so this divergence is
  create and measure only, and the entry should say so.
* **`block_state_zero` is parsed for a dynamic image and then not
  read.** `parse_create_o_options` parses it for both subformats
  deliberately, because qemu-img rejects a non-boolean value for both,
  with the comment "Only a fixed image reads it". So
  `create -f vhdx -o subformat=dynamic,block_state_zero=off` is
  accepted and changes nothing. `parse_o_options` accept-ignores it for
  measure entirely, which is correct and not a divergence: a fixed
  target is dense either way, so the measured numbers do not depend on
  it.

**What phase 12 must measure, not assume.** Both entries above describe
instar. Neither says what qemu-img does, and the plan deliberately does
not guess: whether qemu-img honours `log_size` by writing a different
log region, and what it does with `block_state_zero=off` on a dynamic
image, are the facts the entries turn on. They are measured with the
static builds in step 12b before a word of either entry is written.

### Corrected at source

The master plan's phase 12 paragraph and the `docs/plans/index.md` row
are corrected in this phase's first commit, so the next reader does not
re-derive the above: `CHANGELOG.md` is recorded as already complete,
the `ARCHITECTURE.md` trigger is restated as a roster correction, and
the convert-layout quirk is marked as written in phase 8. A later step
does not need to redo this.

## Decisions

1. **`CHANGELOG.md` is out of scope.** The survey found every phase
   from 7 to 11 wrote its own `[Unreleased]` entry, including fixed
   create, fixed convert, fixed measure, the cross-version baselines
   and the fuzzing. There is nothing to add, and the alternative --
   rewriting correct entries into a single summary -- would lose the
   per-change detail a reader of the released changelog wants.

2. **`ARCHITECTURE.md` gets a two-entry roster correction and nothing
   else.** Not the component-description review the master plan's
   wording implies. The page is loaded into every session, so a
   documentation phase is exactly the wrong moment to let it grow.

3. **The two quirks entries are measured before they are written, and
   each cites the qemu-img version it was measured with.** This is the
   master plan's standing constraint, and it is the reason 12b is a
   separate step at a heavier model than the roster edits: the cost of
   a wrong entry here is paid by whoever next triages a VHDX
   divergence.

4. **An audit step runs first and changes nothing.** 12a enumerates
   every documentation claim about VHDX output capability, VHDX block
   size, and the creatable/convertible format rosters, across `docs/`,
   `README.md`, `AGENTS.md`, `ARCHITECTURE.md` and `.claude/skills/`.
   The edit steps then work from that list.

   This is the decision most likely to be argued with, because the
   survey already found the five pages that are wrong and an audit
   looks like re-doing it. The argument for it is the one phase 11
   learned three times over: the survey checked one axis -- the words
   "fixed" and "preallocation" -- and found the per-phase pages clean
   on that axis, which is not the same as clean. The "32 MiB blocks"
   claim is the proof. It was not found by looking for "fixed"; it was
   found by reading the section that the first finding pointed at, and
   nothing in the survey rules out a third page carrying the same
   number. A grep for the shape costs one cheap step and converts
   "the five pages I found" into "the pages that make this class of
   claim". If 12a's list is just the survey's five, that is a one-line
   result and the phase proceeds unchanged.

5. **`docs/format-internals.md` gains the VHDX layout facts but does
   not duplicate `docs/convert.md`.** The per-format page says what the
   fixed layout *is* -- every payload entry `FULLY_PRESENT` at its
   fixed offset, the data start, zero blocks left as holes -- and
   points at `docs/convert.md` and `docs/create.md` for the command
   surface. Two pages describing one option is how they come to
   disagree, which is the defect this phase is fixing.

6. **No new tests, fixtures or code.** The only executable output is
   12b's measurement transcripts, which are evidence for the prose and
   are recorded in the execution notes rather than committed as a
   fixture.

## Step plan

12a runs first; 12b is independent of it and may run at the same time.
12c, 12d and 12e all consume 12a's list and each run
`pre-commit run --all-files`, so they run in sequence rather than
together -- two steps running that gate in one worktree trip over each
other's work.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 12a | medium | sonnet | none | Audit only, change no file. Enumerate every documentation claim in this repository about (i) which VHDX subformats instar writes, (ii) the VHDX block size or its default, and (iii) the roster of creatable / convert-output formats. Search `docs/`, `README.md`, `AGENTS.md`, `ARCHITECTURE.md` and `.claude/skills/`. Grep for the shape, not one word: `32 MiB`/`32MB`/`32 MB` near vhdx, `[Dd]ynamic VHDX`, `subformat`, `block.size`, `monolithic`, `[Cc]reatable`, `preallocation`, and the format-roster tables. For each hit report file, the claim in one line, and whether it agrees with `vhdx::default_block_size` in `src/crates/vhdx/src/lib.rs` and with the subformats `create` and `convert` actually accept (read `parse_create_o_options` and the `--subformat` handling in `src/vmm/src/main.rs`). Output one markdown table. Do not fix anything, and do not report a claim as wrong without naming the code that contradicts it. |
| 12b | high | opus | none | Measure, then write two new `docs/quirks.md` entries. Measure first, with the static per-version qemu-img builds in `instar-testdata` (see the `qemu_pins` helper and `docs/testing.md` for how the pinned builds are invoked; do not use the host qemu-img): (1) does `qemu-img create -f vhdx -o log_size=<n>` produce a different file from the default, and if so what differs -- compare at two sizes and at least two `log_size` values, including one below and one above qemu's default 1 MiB; (2) what does `qemu-img create -f vhdx -o subformat=dynamic,block_state_zero=off` produce, compared with `block_state_zero=on` and with the option absent. Then write the entries. instar's side is already established and is not to be re-derived: `parse_create_o_options` accept-ignores `log_size` and parses `block_state_zero` for both subformats while only a fixed image reads it; `parse_o_options` (measure) accept-ignores both; `convert` takes no `-o`, so it is outside both entries. Follow the house style of the existing VHDX entries in `docs/quirks.md` -- a heading, what each tool does, the measured transcript, and the consequence for a user -- and cite the qemu-img version each measurement used. Do not state what qemu does from reasoning; if a measurement cannot be made, say so in the entry and in your report rather than filling the gap. Record every transcript in your report. |
| 12c | medium | sonnet | none | Rewrite the `vhdx` section's "Supported" list in `docs/format-internals.md` and add a short fixed-subformat paragraph. The list must name dynamic and fixed, and must not state a single block size: the default is size-scaled, per `vhdx::default_block_size` (64 MiB above 32 TiB, 32 MiB above 100 GiB, 16 MiB above 1 GiB, 8 MiB at or below), and the configurable range is 1 MiB to 256 MiB. The new paragraph says what the fixed layout is -- every payload BAT entry `PAYLOAD_BLOCK_FULLY_PRESENT` at its fixed offset, all-zero blocks left as holes so the file stays sparse, file length the data start plus the virtual size -- and refers to `docs/create.md` and `docs/convert.md` for the command surface rather than repeating the options. Leave the CRC-32C, metadata, overlap-rule and region-cap prose alone: it is current. Work from 12a's list: fix every `docs/format-internals.md` item on it, not only the ones named here. Do not cite a source line number anywhere in `docs/` -- the `docs-line-references` pre-commit hook rejects it, in the `file.rs:NNN` form and in prose; name the function or constant instead. Run `pre-commit run --all-files` and report the hook list. |
| 12d | medium | sonnet | none | Correct the output side of `docs/format-coverage.md`. The "Conversion Output Format Support" table's `vhdx` row must name dynamic and fixed (keep its block-size clause, which is correct); the "Output side" prose that already names vmdk's three subformats must name the VHDX subformats too, and must state that `preallocation=off|falloc|full` is accepted and is a no-op on vmdk, vpc and vhdx while `metadata` is refused -- which is qemu-img's own behaviour, so it is parity rather than a divergence. Do not add a subformat column to the `create / convert-output / dd-output` roster table; its `vhdx | ✓` cell is accurate. Work from 12a's list and fix every `docs/format-coverage.md` item on it. No source line numbers in `docs/` (the `docs-line-references` hook). Run `pre-commit run --all-files`. |
| 12e | low | sonnet | none | In `ARCHITECTURE.md`, correct the one sentence beginning "Creatable target formats are": `vmdk` must name `monolithicFlat` alongside monolithicSparse and streamOptimized, noting it is written on the host, and `vhdx` must read "(dynamic and fixed)" as `vpc` already does. Change nothing else in the file and add no new paragraph -- it is loaded into every session and is an index, not a reference. If 12a's list names other `ARCHITECTURE.md` or `README.md` claims, report them and stop rather than fixing them: that is a scope decision for the management session. Run `pre-commit run --all-files`. |

## Risks and mitigations

* **A measured claim in 12b is wrong, or is measured with the host
  qemu-img instead of a pinned build.** This is the expensive failure:
  it lands a confident, wrong statement on the page people trust.
  Mitigation: the management session re-measures one case from each of
  12b's two entries with a pinned build before committing, and rejects
  the step if a transcript is missing or its version is unstated. The
  brief names the pinned-build requirement explicitly because the host
  binary is the path of least resistance.

* **12b cannot measure something and fills the gap by reasoning.** A
  plausible sentence about qemu is indistinguishable from a measured
  one once it is on the page. Mitigation: the brief says to report the
  gap instead, and the definition of done requires a version string
  beside each claim, which a reasoned claim cannot produce.

* **The documentation phase grows into a rewrite.** Five pages of
  factual corrections is a small change; `docs/quirks.md` alone is over
  5,000 lines and invites tidying. Mitigation: each brief states what
  not to touch, 12e is told to stop and report rather than widen, and
  the definition of done counts changed files.

* **The `docs-line-references` hook rejects an edit late.** It covers
  `docs/` excluding `docs/plans/`, and since `de08bfa6` it catches
  prose forms -- "(line NNN)", "lines NNN-NNN", "at line NNN" -- as
  well as `file.rs:NNN`. The prose half is the trap: it is newer than
  most of this plan's phases, so a habit formed earlier in the plan
  passes review and fails the gate.
  Mitigation: both editing briefs name it and say to name functions and
  constants instead; it runs in `pre-commit`, so the gate catches it
  before review rather than in CI.

* **Two of 12c, 12d, 12e run at once and collide in the gate.** They
  touch different files but share one worktree and all run
  `pre-commit run --all-files`. Mitigation: the step plan says they run
  in sequence, and says why.

## Definition of done

Each item is a command and its expected result, run from the phase
worktree.

* `grep -n '32 MiB blocks' docs/format-internals.md` matches nothing.
* The `vhdx` section of `docs/format-internals.md` contains both
  `dynamic` and `fixed`, and `grep -c 'subformat' docs/format-internals.md`
  is greater than zero.
* No page states the VHDX default block size in a way that disagrees
  with `vhdx::default_block_size`: every documentation hit for
  `32 MiB`, `32MB` or `32 MB` within a VHDX context either states the
  full scaled rule or names the size band it applies to. Checked
  against 12a's list, with each item marked fixed or deliberately left.
* The "Conversion Output Format Support" `vhdx` row in
  `docs/format-coverage.md` names both subformats, and the output-side
  prose names the VHDX subformats and the no-op `preallocation`
  behaviour.
* `ARCHITECTURE.md`'s "Creatable target formats are" sentence names
  `monolithicFlat` and names `vhdx` as dynamic and fixed, and
  `git diff --stat origin/develop -- ARCHITECTURE.md` shows no more
  than that sentence's lines changed.
* `docs/quirks.md` has one entry for `log_size` and one for dynamic
  `block_state_zero`; each states what instar does, what qemu-img does,
  and the qemu-img version the latter was measured with; and each
  measurement's transcript appears in the execution notes.
* `grep -n 'log_size' docs/quirks.md` matches outside the existing
  pasted transcript -- that is, the word appears in prose of its own.
* No new `docs/quirks.md` entry claims `convert` accepts `-o`.
* `pre-commit run --all-files` passes, and the report names every hook
  that ran including `docs-line-references`.
* `CHANGELOG.md` is unchanged by this phase:
  `git diff --stat origin/develop -- CHANGELOG.md` is empty.
* No file outside `docs/`, `ARCHITECTURE.md` and
  `docs/plans/PLAN-bat-population*.md` is changed:
  `git diff --name-only origin/develop` lists nothing else, and in
  particular no file under `src/` or `tests/`.
* No documentation this phase writes cites a plan phase, step or
  decision number, or a `PLAN-*.md` file.
* 12a's audit table is recorded in the execution notes, and every row
  is marked fixed, already-correct, or deliberately left with a reason.

## Execution notes

Implemented 2026-11-11 (+1100), in one session, on branch
`bat-population-phase-12-docs` from `41f74d40`.

### Back brief answers

| Question | Answer |
|---|---|
| Run 12a, or go straight to the survey's five pages? | Run it as planned |
| Is `CHANGELOG.md` really finished? | Yes, leave it alone |
| May 12b run the pinned static qemu-img builds? | Yes |
| May this session push and open the pull request? | Yes |

The last answer is why this phase does not stop at the commit.

### Step order, and the step that was added

12a and 12b ran at the same time: 12a changes no file, so it could not
collide with 12b's write to `docs/quirks.md`. 12c, 12d, 12e and 12f ran
one after another, because each runs `pre-commit run --all-files` in the
one shared worktree and two of those in flight trip over each other.

**12f is not in the step plan above.** The management session added it
after 12a reported, because the audit found stale claims on two pages
the survey had never opened -- `docs/guest-architecture.md` and
`docs/configuration.md`. Both are cross-cutting rather than per-phase
pages, so they fall inside this phase's remit, and the four corrections
are factual one-liners rather than rewrites. It is a separate step so
that the scope growth is visible in the history instead of arriving
inside an unrelated diff.

### Decision 4 was worth its step

The decision argued for an audit on the grounds that the survey had
checked one axis -- the words "fixed" and "preallocation" -- and that
nothing ruled out another page carrying the stale 32 MiB default. That
is exactly what it found: `docs/guest-architecture.md` gives the VHDX
writer's default block size as 32MB, and no search for "fixed" would
have reached it, because that page does not use the word.

It also found a claim worth more than the rest of the phase put
together: the same page said the non-qcow2 sparse formats *reject*
non-`off` preallocation. They accept it. That one predates this plan,
and it qualifies the phase's own headline finding -- the per-phase
constraint held for the pages each phase owned, not for every page its
change made wrong.

### 12a's audit table

Rows the audit found wrong, and what became of each:

| File | Claim | Outcome |
|---|---|---|
| `docs/format-internals.md` | creatable: vhdx dynamic only | fixed, 12c |
| `docs/format-internals.md` | creatable: vmdk sparse + streamOptimized only | fixed, 12c |
| `docs/format-internals.md` | "Dynamic VHDX ... 32 MiB blocks" | fixed, 12c |
| `ARCHITECTURE.md` | creatable roster omits monolithicFlat and fixed | fixed, 12e |
| `docs/format-coverage.md` | convert-output vhdx row: dynamic only | fixed, 12d |
| `docs/format-coverage.md` | VHDX note: dynamic output, one block-size tier | fixed, 12d |
| `docs/format-coverage.md` | output-side prose names no VHDX subformat, no preallocation | fixed, 12d |
| `docs/guest-architecture.md` | VHDX writer "default 32MB", dynamic only | fixed, 12f |
| `docs/guest-architecture.md` | non-qcow2 formats "reject non-`off` preallocation" | fixed, 12f |
| `docs/guest-architecture.md` | "vhdx default block_size" a live writer divergence | fixed, 12f |
| `docs/configuration.md` | `-O FORMAT` offers "(raw, qcow2)" | fixed, 12f |

Rows the audit checked and found correct, so deliberately left: the
`create / convert-output / dd-output` roster table and the resize rows
in `docs/format-coverage.md`; `docs/dd.md`'s block-size tiers;
`AGENTS.md` and `README.md`, which name write formats without claiming a
subformat; `docs/create.md`, `docs/convert.md`, `docs/measure.md`,
`docs/resize.md`, `docs/info.md`, `docs/quirks.md` and
`docs/testing.md`; `docs/commentary/reading-order.md`; and
`docs/guest-architecture.md`'s resize and dd-output claims.

Two rows the audit could not resolve and the management session then
settled. `docs/configuration.md`'s `-O` cell was resolved by its own
section heading, "Convert Options", which makes it instar's flag table
rather than an integration wish-list, and then against
`parse_output_format`, which accepts five formats and lists all five in
its error message -- so it was fixed in 12f. `docs/usage.md`'s format
list is an OpenStack "must support" set rather than a roster of what
instar writes, so it is left alone.

### Measurements

Every qemu-img claim this phase added was measured against the pinned
static builds under `instar-testdata/qemu-img-binaries/x86_64/`. The
host qemu-img was never used. Transcripts are in this session's
scratchpad under `step-12b/transcripts/`, covering `log_size` and
dynamic `block_state_zero` at 10.2.0 and 6.0.0, their structural
comparisons at 16 MiB and 4 GiB, the edge cases, and a sweep of all 80
builds (`sweep-all-versions.txt`, 80 versions x 9 probes).

`log_size` is honoured by qemu-img: the header's `LogLength` and the
`bat` and `metadata` region offsets move with it, carrying the file
length, and nothing else changes. A value that is not a whole MiB is
rounded up rather than refused; `log_size=0` is refused and leaves a
zero-length file.

`block_state_zero=off` is honoured by qemu-img on a *dynamic* image,
which this plan had not assumed either way: every payload BAT entry
becomes `PAYLOAD_BLOCK_NOT_PRESENT` instead of `PAYLOAD_BLOCK_ZERO`,
and nothing else changes. instar accepts the option and ignores it for
a dynamic image, so this is a real divergence rather than a shared
no-op.

The management session re-measured one case from each entry
independently on 6.0.0 and reproduced both, and separately measured the
preallocation parity that 12d and 12f assert, because the brief had
asserted it without evidence. That measurement is worth recording for
the next person, because byte comparison is the wrong oracle and says
so loudly: every qemu-img create writes fresh random identifiers, so
two creates with the *same* mode differ too. With identical output
filenames and the per-creation fields accounted for, `off`, `falloc`
and `full` are indistinguishable -- vmdk differs only in its `CID`, vpc
in its unique ID and the footer copy of it, vhdx in the two header
write GUIDs and one metadata GUID, and all three VHDX modes compare
structurally equal through `tests/helpers/vhdx_structure.py`.
`metadata` is refused on all three formats.

### Definition of done, extended

The definition of done above was written against five pages, and the
audit found seven. Four items were added when 12f was added, and they
are marked as such in the report: a criterion written after seeing the
work is weaker evidence than one written before it.

* `docs/guest-architecture.md` states no single VHDX default block size,
  and names both subformats for the VHDX writer.
* `docs/guest-architecture.md` does not say the non-qcow2 sparse formats
  refuse a non-`off` preallocation mode.
* `docs/guest-architecture.md` does not list a vhdx item among the
  writer divergences carried as per-case skips.
* `docs/configuration.md`'s `-O FORMAT` cell names all five output
  formats `parse_output_format` accepts.

### Corrections made at source

The master plan's phase 12 paragraph and the `docs/plans/index.md` row
were corrected in this phase's planning commit, before any step ran:
`CHANGELOG.md` recorded as already complete, the `ARCHITECTURE.md`
trigger restated as a roster correction, and the convert-layout quirk
marked as written by phase 8.

### Review corrections to step output

Each editing step's output needed at least one correction, and all but
one were the same shape: a sentence that would have created a *fresh*
disagreement between two pages, which is the defect this phase exists to
remove.

* 12c dropped the power-of-two constraint from the block-size range that
  `docs/create.md` states, omitted `check` from the fixed VHDX line
  where `docs/check.md` documents `check` accepting fixed images
  qemu-img calls truncated, and named measuring while linking only
  create and convert.
* 12e and 12f each left one line past the file's wrap width after an
  edit, rewrapped by the management session.

Two steps reported that a claim came from their brief rather than from
the code -- 12c the block-size range, 12d and 12f the preallocation
parity and the five output formats. Both flags were warranted, and both
gaps were the brief's rather than the step's. The parity claim in
particular was asserted without evidence by the management session and
then had to be measured.

## Back brief

Answer before any step runs:

1. **Is decision 4 worth its step?** 12a is a cheap audit that may
   return only the five pages the survey already found. Run it, or go
   straight to the edits on the survey's list?
2. **Is decision 1 right that `CHANGELOG.md` needs nothing?** The
   survey found a per-phase `[Unreleased]` entry for every phase from 7
   to 11. A reader of the release notes gets five entries about fixed
   VHDX rather than one. Leave them, or add a summary entry?
3. **Does 12b need a pinned-build run at all**, or is there an existing
   recorded measurement of qemu's `log_size` and dynamic
   `block_state_zero` behaviour that it should cite instead? If the
   answer is that it must measure, confirm the session may run the
   static builds.
4. **May this session open the pull request** when the phase is done,
   or does it stop at the push?

## Out of scope

* `CHANGELOG.md` (decision 1).
* Any code, test or fixture change. A documentation phase that fixes a
  bug it found is a phase that was not reviewed as a bug fix; anything
  12a or 12b turns up that is a defect rather than a stale sentence is
  filed as an issue and named in the execution notes.
* The per-phase pages -- `docs/create.md`, `docs/convert.md`,
  `docs/measure.md`, `docs/testing.md` -- except where 12a finds one of
  them carrying a claim in the audited class. They were kept current by
  their own phases.
* The `docs/quirks.md` entries for divergences this plan did not
  create, and the existing "VHDX partial last block" section, which is
  phase 8's and is correct.
* Anything in the master plan's Future work list, including
  `block_state_zero` for dynamic VHDX as a *feature* (this phase
  documents that it is accepted and ignored, which is not the same as
  implementing it) and `log_size` being honoured.
