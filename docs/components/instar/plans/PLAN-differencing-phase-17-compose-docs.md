# Differencing phase 17: composition documentation

## Prompt

Plan phase 17 of `PLAN-differencing.md`. Phase 16 landed in `d7a4973f`
(#652). Phases 11 to 16 built composition, rolled it out across the
chain-walking operations, settled which operations still refuse, and
tested and fuzzed the result. This phase is the documentation that
describes all of it.

The phase plan is the deliverable; implementation is a separate ask.

## Planning effort

**High**, and almost all of it spent in the survey, which found the
master plan's phase 17 bullet pointing at the wrong work. Most of what
it reserves for this phase was taken by phase 14 fourteen months of
plan-time earlier, and the two genuinely false statements left in the
tree are in files the bullet does not name — one of them in
`ARCHITECTURE.md`, which is the first page a reader meets.

Review effort: **medium**. No production code changes. The judgement
is in what is now true, which the survey establishes by measurement,
and in Decision 3's refusal to rewrite history.

## Scope

**In scope.** Corrections, each a statement in the tree that is false
today, plus the master-plan correction already made in this phase's
planning commit. The survey found four; execution found two more:

* `ARCHITECTURE.md`'s claim that instar refuses to compose a
  differencing child on read.
* `docs/chain-config.md`'s `### VHD (future)` section.
* The read-side rows of `docs/format-coverage.md`, which show a clean
  sweep of ticks for vhd/vpc and vhdx with nothing to say that a
  differencing sub-state refuses three of those operations.
* `docs/info.md`'s deferral of a known limitation to a plan whose work
  is now finished.

Step 5, added during execution, extends that to the rest of the class
rather than the reported instances: the live plan pointers in
`docs/quirks.md` and `docs/testing.md`, and `docs/guest-architecture.md`'s
claim that `VhdxState::init` already rejects a differencing VHDX. See
"The sweep was under-scoped" below.

**Out of scope.**

* **`CHANGELOG.md`.** See Decision 3.
* **`AGENTS.md`.** No convention changed. The project rule is that
  `AGENTS.md` changes only when how-to-work-here changes, and it is
  loaded into every session, so a line costs context on every task.
* **Anything phase 14 already wrote.** `docs/chain-discovery.md` and
  the compose section of `docs/format-coverage.md` are current; the
  survey says so by inspection and this phase must not churn them.
* **New pages.** Everything lands in a page that exists.
* **Issues #641, #642, #643, #653 and #654.** Documenting what the
  operations do today is this phase's job; changing it is not.
* **The push audit** — phase 18.

## What the survey found

The master plan's phase 17 bullet
(`docs/plans/PLAN-differencing.md:683-697`) is stale in three ways,
and the third changes the shape of the phase.

### The line counts are wrong, which is the harmless part

It says the two chain pages "already exist (209 and 210 lines)". They
are now 413 and 283. That matters only as a signal of how much has
happened since the bullet was written.

### Phase 14 already did most of it

Commit `b0ba64b8` ("Say what composes and what does not.", in #630,
phase 14) is the one the bullet describes without knowing it. Its own
message records the scope: the per-operation pages for `convert`,
`dd`, `compare` and `bench`; a new section for `rebase`; `map`,
`measure` and `check` keeping their limitations and saying which
operations do not share them; the chain discovery page gaining the
capability and the invariant behind it; and "four pages outside the
planned list" that had asserted nothing in instar composes a
differencing source.

So, checked against the tree today:

* `docs/chain-discovery.md` carries the capability (`:210-214`), the
  per-operation list (`:247-272`) and a Known limitations section,
  "A differencing VHD or VHDX parent is resolved only by the
  operations that read it" (`:278-294`), which states the invariant
  that an operation's refusal must not depend on whether the parent
  file happened to exist. Current. Nothing owed.
* `docs/format-coverage.md`'s "VHD/VHDX differencing (parent
  composition)" section (`:282-314`) names the five composing
  operations, the three refusing ones and `info` as the exception,
  and records the divergence in both directions. Current after phase
  15. Nothing owed.
* `docs/guest-architecture.md:448-458` describes the mechanism in
  prose -- "composing each sector from whichever image in the chain
  owns it" -- with the per-op split. Current.

### The two false statements are in files the bullet does not name

**`ARCHITECTURE.md:131-135`** is the important one:

> A differencing VHD (`disk_type=4`) or VHDX (`HasParent` set) child
> is not the same thing as a backing-file reference: instar emits one
> from `create -b`, but refuses to compose one on read rather than
> merging the parent's sectors into the result, a composition
> deferred and tracked in
> [docs/plans/PLAN-differencing.md](/components/instar/plans/PLAN-differencing/).

Both halves are wrong. Five operations compose, and the composition is
not deferred -- it is built, tested and fuzzed. It is also a landed
page pointing at a plan file for completed work, which the project's
own rule forbids: `ARCHITECTURE.md` is a summary and an index into
`docs/`, and a reader sent to a plan learns what was intended rather
than what exists.

This is the single highest-value line in the phase. `ARCHITECTURE.md`
is where a new reader and a new agent both start, and the read-side
composition path is exactly the "shape of the system" change the
project rule says `ARCHITECTURE.md` exists to record.

**Swept rather than fixed in place.** `grep -rn 'refuses to
compose\|composition deferred\|deferred and tracked\|does not
compose\|no composition' *.md docs/*.md` excluding `docs/plans/`
returns those two lines and nothing else, so the class has exactly one
member outside the changelog.

**The sweep was under-scoped, found during execution.** That grep and
the one in the Definition of done below were run against the files
this survey was already considering, and their exclusion lists say so:
`docs/quirks.md` holds five verbatim quotes of error messages instar
emitted at commit `10ab838`, framed "at the time of this record", plus
a provenance block citing the phase-01 structure pin, and
`docs/testing.md` holds one more provenance citation. Those stay, for
Decision 3's reason exactly. But four live pointers were hiding behind
them -- `docs/quirks.md:4356` assigning path handling to "the
composition phases" in the present tense, `docs/quirks.md:4836`
deferring the *same* limitation as `docs/info.md` to the finished
plan, and two in `docs/testing.md` -- and a fifth false statement in a
file no phrase in the list could reach: `docs/guest-architecture.md`
said "vhdx differencing is already rejected by `VhdxState::init`",
which phase 14 falsified and which contradicts the next sentence of
its own paragraph. Step 5 fixes those; the exclusion lists below now
name what stays and why.

### `chain-config.md`'s VHD section is future tense for a built thing

`docs/chain-config.md:267-271`:

> ### VHD (future)
>
> VHD stores its footer at the last 512 bytes of the file. Like VMDK,
> the chain reader will need `actual_size` to locate the footer
> correctly.

The VHD chain reader was built in phases 12 to 14. The section is also
wrong about what it predicted: `VhdState::init`
(`src/crates/vhd/src/lib.rs:1488-1512`) reads sector 0 first, because
a dynamic VHD carries a footer *copy* there, and only falls back to
the last sector -- located as `input_capacity - 1`, from the virtio
capacity -- for a fixed VHD. It never consults `ChainConfig`'s
`actual_size`; where the crate uses a name like `actual_size`
(`:1557`) it is a locally derived `input_capacity * sector_size`
bound for range checks, not the field this page documents.

There is no VHDX section at all, though `VhdxState::init`
(`src/crates/vhdx/src/lib.rs:2180-2288`) is implemented and derives
its bound the same way.

### The read-side table says nothing about the sub-state

`docs/format-coverage.md:115-116`:

```
| vhd / vpc | ✓ | ✓‡ 9 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| vhdx      | ✓ | ✓    | ✓ | ✓ | ✓ | ✓ | ✓‡ 10 | ✓ |
```

A clean sweep of ticks. `check`, `map` and `measure` refuse a
differencing source, and the table carries no marker saying so -- the
fact lives only in prose 170 lines further down. A reader scanning the
matrix, which is what a matrix is for, gets the wrong answer. This is
the piece phase 16 explicitly deferred here (its scope section names
"the read-side rows of `docs/format-coverage.md`"), and the survey
confirms it is the only part of that file still owed.

### `info.md` defers to a plan that has finished

`docs/info.md:112-117` describes a real limitation -- a parent
recorded in Windows convention is reported with an "actual path" that
cannot exist, affecting both formats -- and then says fixing it
"belongs with the composition work in
[PLAN-differencing.md](/components/instar/plans/PLAN-differencing/)". The composition
work is finished and this was not fixed, so the deferral now points at
nothing. No open issue tracks it: `gh issue list --search 'info actual
path parent locator'` returns none.

### Corrected at source

The master plan's phase 17 bullet and the `index.md` row are corrected
in this phase's planning commit, per the planning skill, so a later
step does not rediscover the same staleness. The bullet now says what
phase 14 took and what is left.

## Decisions

1. **This is a correction phase, not an authoring phase.** Scope is
   the four false statements the survey found, not the master plan's
   file list. Writing fresh prose about composition into
   `docs/chain-discovery.md` would duplicate what phase 14 put there,
   and duplicated documentation is how two pages come to disagree.

2. **`ARCHITECTURE.md` is in scope; `AGENTS.md` is not.** The project
   rule is that `ARCHITECTURE.md` changes when the shape of the system
   changes and `AGENTS.md` when a convention does. A read-side
   composition path that did not exist is a shape change. No
   convention changed.

3. **`CHANGELOG.md` is not rewritten.** It carries the old refusal in
   two forms: a verbatim quote of the error message instar used to
   emit, "composition is deferred (see PLAN-differencing.md)"
   (`:829`), and entries at `:261`, `:273` and `:291` that link the
   plan. Both are correct as history. A changelog records what a
   release did, and editing it so that past releases appear to have
   had today's behaviour is the one kind of documentation error that
   cannot be detected by reading the tree. The plan links in those
   entries stay for the same reason: at the time, the plan *was* where
   the deferral was tracked.

4. **`info.md`'s deferral becomes an issue reference, after
   measurement.** Check whether the Windows-convention "actual path"
   behaviour still reproduces before writing anything. If it does,
   file the issue and point at it; if it was fixed somewhere in phases
   11 to 16, delete the limitation instead. Do not simply reword the
   pointer -- that would leave a known limitation tracked nowhere,
   which is worse than the stale link.

5. **The table gets a note, not a new row.** `format-coverage.md`
   already says, in the compose section, that differencing is "a
   sub-state of the vhd/vpc and vhdx rows above, not a separate
   format, so it gets no row of its own". Honour that: mark the three
   refusing cells and point the note at the section that already
   exists.

6. **Every correction is checked against the code, not against another
   document.** Phase 15 changed what `check`, `map` and `measure` do
   and phase 14's prose predates it, so a correction copied from a
   sibling page can be confidently wrong. The step briefs name the
   functions to read.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | medium | opus | none | `ARCHITECTURE.md:131-135`. Replace the claim that instar "refuses to compose one on read" and the plan-file link. State what is true: `create -b` emits a differencing child, and on read `convert`, `dd`, `compare`, `bench` and `rebase` compose it against its parent at sector granularity, while `check`, `map` and `measure` refuse by name and `info` reports the parent without composing. Verify that split against the code before writing it (D6) -- the refusal sites and the composing readers, not `docs/format-coverage.md`, which is a sibling document. Keep it to the two or three sentences the surrounding paragraphs use; `ARCHITECTURE.md` is an index, so link `docs/chain-discovery.md` for the capability and `docs/format-coverage.md` for the per-op matrix instead of explaining either. Then re-run the class sweep from the survey (`grep -rn 'refuses to compose\|composition deferred\|deferred and tracked\|does not compose\|no composition' *.md docs/*.md`, excluding `docs/plans/` and `CHANGELOG.md`) and confirm it returns nothing. |
| 2 | medium | opus | none | `docs/chain-config.md:267-271`. Replace `### VHD (future)` with a present-tense VHD section and add a VHDX one, both in the shape of the QCOW2 and VMDK notes above them. Say what is actually true, read from `VhdState::init` (`src/crates/vhd/src/lib.rs:1488-1512`) and `VhdxState::init` (`src/crates/vhdx/src/lib.rs:2180-2288`): neither uses `ChainConfig`'s `actual_size`, because a dynamic VHD carries a footer copy at sector 0 and the fixed fallback locates the footer from the device capacity (`input_capacity - 1`). Say so explicitly rather than deleting the paragraph -- the VMDK note above explains why *it* needs `actual_size`, and a reader comparing the two should learn why VHD does not. Mention what these readers do need that the others do not, the per-block sector bitmap that selects child or parent per sector; the protocol page is the right home for that and no page currently explains the selection mechanism in one place. Do not restate the per-operation split, which is `:244-247`'s job a few lines above. |
| 3 | low | sonnet | none | `docs/format-coverage.md:115-116` (D5). Mark the `check`, `map` and `measure` cells on the `vhd / vpc` and `vhdx` read-side rows with a note, following the file's existing `✓‡ N` / `R‡ N` idiom and numbering, and add the note text pointing at the "VHD/VHDX differencing (parent composition)" section at `:282`. Read that section first and keep the wording consistent with it. Check whether the In-place ops and Output side tables (`:136-137`, `:160-161`) need the same marker; `rebase` composes and `commit` does not, so decide by reading the section rather than by symmetry. Change nothing else in this file -- the compose section is current and must not be churned. |
| 4 | medium | sonnet | none | `docs/info.md:112-117` (D4). First measure: build a differencing VHDX whose parent locator is a Windows-convention path and run `instar info` on it, and do the same for a VHD unicode parent name, to see whether the "actual path" output described there still reproduces. If it does, file an issue with the transcript and replace the plan-file link with the issue reference, keeping the explanation; the paragraph about `backing file:` being correct in every case stays either way. If it no longer reproduces, delete the limitation and say in the commit message which phase fixed it. Do not leave the plan link in place: phase 17 is the last documentation phase, so a pointer to a finished plan has no later owner. |

| 5 | medium | opus | none | Added during execution, after the Definition of done's two greps failed for a reason the survey had not seen. Sweep the class rather than the four reported instances. Fix the live pointers at `docs/quirks.md:4356` (assigns path handling to "the composition phases", which are complete) and `docs/testing.md:1572` and `:1672` (send a reader to the plan to understand the current suite and the guarded path), retarget `docs/quirks.md:4836` at the issue step 4 filed so the two pages describing one limitation agree, and correct `docs/guest-architecture.md:585`, which claims `VhdxState::init` already rejects a differencing VHDX -- it reports `has_parent` instead (`src/crates/vhdx/src/lib.rs:2303-2309`), which is why `map` needed its own arm, as the very next sentence says. Leave every historical record alone: the five quoted error messages in `docs/quirks.md` and the two provenance citations are correct as history, and editing them is the one documentation error that cannot be caught by reading the tree. |

Commits: the phase 16 close-out and this plan (already made); then one
per step. Every commit passes `pre-commit run --all-files`.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| A correction is copied from a sibling document that phase 15 made stale, and is confidently wrong | D6, and each step brief names the source files to read. Step 1 in particular must not take the five-and-three split from `format-coverage.md`. Owner: each step. |
| The phase churns `docs/chain-discovery.md` because the master plan told it to, producing a diff that reviews as busy-work and risks contradicting phase 14 | Scope says explicitly that the page is current and out of scope, and the survey records why. The master plan bullet is corrected in the planning commit so it cannot mislead a later reader. Owner: scope. |
| `CHANGELOG.md` gets "corrected" | D3, with the reasoning stated rather than asserted, since this is the instruction most likely to look like an oversight to a reviewer. Owner: D3. |
| Step 4 finds the limitation fixed, deletes it, and is wrong | The step measures before deciding, with a built fixture rather than a reading of the code. Owner: step 4. |
| The table markers in step 3 drift from the prose section they point at | The brief requires reading the section first and keeping the wording consistent; the DoD checks the two agree. Owner: step 3. |

## Definition of done

Falsifiable, in the order a reviewer can check them:

* `grep -rn 'refuses to compose\|composition deferred\|deferred and tracked\|does not compose\|no composition' *.md docs/*.md`, excluding `docs/plans/`, `CHANGELOG.md` and `docs/quirks.md`, returns nothing. `docs/quirks.md` is excluded for Decision 3's reason and no other: its five hits are inside verbatim quotes of error messages instar emitted at commit `10ab838`, under a heading that says so and in a section that states the behaviour "at the time of this record".
* `grep -rn 'PLAN-differencing' *.md docs/*.md`, excluding `docs/plans/`, `CHANGELOG.md` and `docs/development.md` (whose two hits describe the doc-phrase guard's own test strings), returns exactly nine hits, all provenance or quoted history: `docs/quirks.md:4193`, `:4194` and `:4200` (a block recorded by step 1c of the phase-01 pin), `docs/testing.md:2114` (fixtures packed against that pin), and the five quoted error messages above. No landed page points a reader at this plan for behaviour that exists.
* `grep -rn 'already rejected by' docs/guest-architecture.md` returns nothing, and no landed page says `VhdxState::init` refuses a differencing image.
* `docs/chain-config.md` contains no heading with "(future)" in it, and has a VHDX section.
* The `check`, `map` and `measure` cells of the `vhd / vpc` and `vhdx` read-side rows in `docs/format-coverage.md` carry a note, and that note's text does not contradict the section at `:282`.
* `docs/info.md` either carries no Windows-path limitation, or carries one with a GitHub issue reference and no plan-file link.
* `ARCHITECTURE.md`'s differencing paragraph names which operations compose and which refuse, and that split matches the code -- checked by reading the refusal sites, not another document.
* No change to `AGENTS.md`, `CHANGELOG.md`, `docs/chain-discovery.md`, or the compose section of `docs/format-coverage.md`.
* `pre-commit run --all-files` passes.

## Back brief

One gate, before step 1 writes anything: state the five-composes /
three-refuse / `info`-excepted split **with the file and line of each
refusal site you read to confirm it**, and wait. Every one of the four
steps restates that split or points at it, so if it is wrong the whole
phase is wrong in the same direction, and it is the one fact in this
phase that four separate pages will agree on afterwards whether or not
it is true.
