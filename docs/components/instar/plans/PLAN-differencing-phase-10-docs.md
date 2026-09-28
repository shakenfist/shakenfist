# Differencing phase 10: cross-cutting documentation

## Prompt

Phase 10 of [PLAN-differencing.md](/components/instar/plans/PLAN-differencing/). The master
plan's standing rule is that every phase updates the documentation for
the behaviour it changes in its own pull request, and that phase 10
"exists for the cross-cutting pages, not as a licence to leave the
per-phase pages stale" (`PLAN-differencing.md:585`). This phase settles
what the whole feature says across pages that no single earlier phase
owned, and reconciles the statements that are now false.

## Planning effort

Medium, as the master plan specifies for this phase
(`PLAN-differencing.md:600`). The survey below did the research the
briefs depend on; nothing here turns on format-spec interpretation.

Review effort: medium. The risk in a documentation phase is a
confident sentence that is wrong, not a subtle defect, so the review
that matters is checking assertions against the tree rather than
reading for style.

## Scope

**In scope.** The pages the master plan's success criteria name for
this phase: `docs/create.md`, `docs/format-coverage.md` (output-side
table and divergence notes), `docs/quirks.md`, `docs/resize.md`,
`docs/guest-architecture.md`, `ARCHITECTURE.md`, and the false input
claim in `CHANGELOG.md`. Plus the mangled-phrase corruption the survey
found, which is discussed under decision 4.

**Out of scope.** `docs/chain-discovery.md` and `docs/chain-config.md`,
and the read-side rows and divergence notes of `docs/format-coverage.md`
— the master plan assigns those to phase 16, because phase 10 documents
a refusal that phase 14 removes (`PLAN-differencing.md:504`). Do not
pre-empt that: the refusal is the current truth and phase 10 states it
as such.

Also out of scope: any behaviour change. If this phase finds a defect,
it records it and files an issue. Two are already open and are not to
be fixed here — #593 (the extractor drops manifest fixtures over
`MAX_SEED_SIZE`) and #566 (created images share one identity).

## What the survey found

Seven findings. Three of them contradict the master plan, and are
corrected at source in `PLAN-differencing.md` and `docs/plans/index.md`
as part of the planning commit, so a later step must not redo it.

1. **The `CHANGELOG.md:1922` reference is wrong.** The master plan's
   success criteria name that line as carrying the false
   "differencing with backing chains" input claim. Line 1922 is about
   vhdx parent `virtual_size` recovery. The claim is at
   **`CHANGELOG.md:2314`**, in the historical `Input formats:` list:
   `- VHD (fixed, dynamic, differencing with backing chains)`. It has
   drifted 392 lines. Corrected in the master plan.

2. **`docs/chain-discovery.md` and `docs/chain-config.md` already
   exist**, at 209 and 210 lines. The master plan's phase 16 note
   reads as though phase 16 authors them ("at minimum
   `docs/chain-discovery.md`, `docs/chain-config.md`"). They are
   existing pages about qcow2 chain discovery that phase 16 extends;
   `chain-config.md` mentions differencing zero times and
   `chain-discovery.md` four. Corrected in the master plan.

3. **Four of the six named pages are already substantially current.**
   The per-phase documentation rule was actually followed:
   `docs/create.md` carries 21 differencing mentions including the
   `disk_type=4` and parent-locator explanation at :142 and the
   same-format constraint at :269; `docs/format-coverage.md` carries
   divergence note 17 ("vpc / vhdx differencing create — instar-only",
   Measured 2026-09-20) and a "VHD/VHDX differencing (parent
   composition)" section at :282; `docs/quirks.md` carries the
   canonical "VHD/VHDX differencing" section at :4190 that the other
   pages cross-reference; `docs/resize.md` carries 8 mentions
   including the `rejects_differencing_image` note at :196. **Phase 10
   is therefore a reconciliation and gap-filling phase, not an
   authoring one.** Plan it that way: the failure mode here is a
   sub-agent rewriting current, measured prose because the brief said
   "document differencing".

4. **`ARCHITECTURE.md` mentions differencing zero times.** Its "Format
   support" section (:122-128) says "Backing-file references are
   supported on qcow2, vmdk, vpc and vhdx" without distinguishing a
   backing reference from a differencing child, which is the one place
   a reader would look to learn the shape. See decision 3.

5. **`docs/guest-architecture.md` is thin and partly stale.** Two
   differencing mentions, both inside the map operation's description
   at :553-554, framed as that op's own limitation: "refuses sources
   with chain composition (qcow2 backing-file references, vhd
   differencing disks; vhdx differencing is already rejected by
   `VhdxState::init`...)". Phase 4 made refusal a uniform policy
   across ops rather than a per-op quirk, and phases 5-7 added an
   emit path this page does not mention at all.

6. **A mangled-phrase corruption spans five pages, 17 instances.**
   Phase 9g repaired seven of these in `docs/testing.md` and the class
   was not swept further. A bad automated replace spliced the word
   "work" into the middle of a plan filename, so "the PLAN-map work"
   became "the PLAN-m workap":

   | Page | Instances |
   |---|---|
   | `docs/quirks.md` | 7 |
   | `docs/guest-architecture.md` | 6 |
   | `docs/commit.md` | 2 |
   | `docs/bench.md` | 1 |
   | `docs/rebase.md` | 1 |

   Find them with `git grep -nE 'PLAN-[a-z] work[a-z]'`. Two of the
   five pages are in this phase's scope and three are not; see
   decision 4.

7. **`docs/index.md` links every page under `docs/`**, verified by
   iterating the tracked `.md` files and grepping the index for each.
   If this phase creates no new page, `docs/index.md` needs no change
   — and decision 2 says it creates none.

Nothing else the master plan claims about this phase was wrong.

## Decisions

1. **Reconcile the `CHANGELOG.md` claim in place; do not delete it.**
   The line sits in a historical release entry, and changelogs are a
   record of what was said at the time. Amend it to state what is
   actually supported — differencing VHD and VHDX are parsed and
   refused for composition, not composed — rather than rewriting
   history to pretend the claim was never made. A parenthetical
   correction naming the current state is enough.

2. **No new documentation page.** The natural instinct is a
   `docs/differencing.md`, and the user's documentation policy makes
   `docs/` the default home for new material. But `docs/quirks.md`'s
   "VHD/VHDX differencing" section at :4190 is already the canonical
   account, and `create.md`, `format-coverage.md` and `resize.md` all
   cross-reference it by that name. A new page would either duplicate
   it or orphan those four cross-references. Keep the quirks section
   canonical and make every other page point at it.

3. **`ARCHITECTURE.md` gets exactly two sentences, in "Format
   support".** The user's documentation policy says ARCHITECTURE.md
   changes only when the *shape of the system* changes, and most of
   this feature is a capability change that belongs in `docs/`. But
   the existing sentence "Backing-file references are supported on
   qcow2, vmdk, vpc and vhdx" is now actively misleading: a
   differencing child is not a backing reference, instar emits one and
   refuses to compose one, and a reader of that sentence would
   conclude the opposite. Correcting a statement the feature made
   false is within the policy; a section on differencing is not.

4. **Fix all 17 mangled instances, including the three pages outside
   this phase's scope.** This is the decision most likely to be
   argued with, so the reasoning matters. Against: `docs/bench.md`,
   `docs/commit.md` and `docs/rebase.md` have nothing to do with
   differencing, and touching them widens the diff and the review.
   For: the corruption is mechanical, the repair is a literal
   substitution with no judgement in it, the class has already
   demonstrated it does not get swept when left (phase 9 fixed one
   page of six), and a reader hitting "the PLAN-q
   workcow2-write-infrastructure" cannot parse the sentence at all.
   Leaving a known, one-line-each corruption in three pages because
   they belong to a different plan is the incomplete-generalisation
   failure this repository has paid for before. Fix the class.

5. **Add a CI guard for the corruption rather than only fixing it.**
   A one-line grep in the `ci-tooling` job, next to the fuzz
   registration guard phase 9 added. Phase 9 fixed seven instances and
   17 survived; without a guard the next bad replace reintroduces them
   and nothing notices. This is the "make the answer derivable"
   principle: the guard is cheaper than the next sweep.

6. **State the refusal as current truth, with a forward pointer.**
   Phase 14 removes it. Every page that describes the refusal says so
   and links `PLAN-differencing.md`, which is what
   `docs/format-coverage.md:291` already does. Do not hedge the
   refusal into "may currently" prose: it is the behaviour today and
   phase 16 will update these pages when it changes.

7. **Verify, do not re-measure.** `docs/format-coverage.md`'s
   divergence note 17 carries "Measured 2026-09-20". Phase 10 does not
   re-run those measurements; it checks the statements are still
   consistent with the tree and leaves the measurement dates alone. If
   a sub-agent believes a measured claim is wrong, it reports that
   rather than silently restating it — this repository's history is
   that agents assert plausible-but-wrong capabilities
   (`PLAN-differencing.md:609`).

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|---|---|---|---|---|
| 10a | low | haiku | worktree | Repair the mangled-phrase corruption. `git grep -nE 'PLAN-[a-z] work[a-z]'` finds 17 instances across `docs/quirks.md` (7), `docs/guest-architecture.md` (6), `docs/commit.md` (2), `docs/bench.md` (1), `docs/rebase.md` (1). Each is a plan filename with the word "work" spliced into it: "the PLAN-m workap" is "the PLAN-map work", "the PLAN-q workcow2-write-infrastructure" is "the PLAN-qcow2-write-infrastructure work", "the PLAN-f workormat-coverage.md\`" is "the \`PLAN-format-coverage.md\` work". Reconstruct each from the plan file it names — confirm the file exists under `docs/plans/` before writing the name. Do not reflow surrounding paragraphs; change only the corrupted span. Per the repository's no-plan-references-in-landed-code rule, these are prose in `docs/`, not code comments, so the plan names stay. Commit subject: `Repair mangled plan names in the docs.` |
| 10b | low | sonnet | worktree | Add `tools/ci/check-doc-phrases.sh`: fails when `git grep -nE 'PLAN-[a-z] work[a-z]'` matches any tracked file, printing each hit. Model it on `tools/ci/check-fuzz-targets.sh` for structure — `set -euo pipefail`, repo root from `BASH_SOURCE`, a one-line success summary, every hit reported before exiting 1. Write `tools/ci/test-check-doc-phrases.sh` alongside it in the shape of `tools/ci/test-check-fuzz-targets.sh`: a temp tree, exit 0 when clean, exit 1 naming the file and line when a corrupted phrase is planted. Wire both into the `ci-tooling` job in `.github/workflows/functional-tests.yml`, next to the existing "Check the fuzz target registration lists" step. Add one inventory line each to the Scripts list in `docs/development.md`. Depends on 10a; the guard must pass on the repaired tree. Commit subject: `Guard against mangled plan names in docs.` |
| 10c | medium | opus | worktree | Reconcile the false input claim. `CHANGELOG.md:2314` reads `- VHD (fixed, dynamic, differencing with backing chains)` inside a historical release entry's `Input formats:` list. instar parses differencing VHD and VHDX and **refuses** to compose them (`docs/format-coverage.md:282`); it does not read them as chains. Amend the line in place with a parenthetical stating the current position, per decision 1 — do not delete the entry and do not rewrite the surrounding release. Check the adjacent VHDX line at :2315 for the same overclaim and treat it the same way if it has one. Then grep `CHANGELOG.md` for any other input-format claim about differencing composition and report what you find without changing older entries beyond this one. Commit subject: `Reconcile the differencing input claim.` |
| 10d | medium | opus | worktree | Fix the two pages the survey found thin or stale. `ARCHITECTURE.md` mentions differencing zero times and its "Format support" section (:122-128) says "Backing-file references are supported on qcow2, vmdk, vpc and vhdx", which a reader will take to mean differencing children compose. Per decision 3, add **two sentences at most** to that section: a differencing VHD/VHDX child is distinct from a backing reference, instar emits one via `create -b` and refuses to compose one on read. No new section, no expansion elsewhere in the file. `docs/guest-architecture.md:553-554` frames differencing refusal as the map operation's own limitation; phase 4 made it uniform policy and phases 5-7 added an emit path the page never mentions. Correct the map paragraph to point at the uniform policy, and add the create op's differencing emit to that operation's entry in the same list — match the surrounding entries' style (binary size figures, call-table function names) and do not invent figures: read them from the file or omit them. Commit subject: `Document differencing in the architecture pages.` |
| 10e | high | opus | worktree | The consistency pass, and the judgement-heavy step. Read all of `docs/create.md`, `docs/format-coverage.md`, `docs/quirks.md` (the "VHD/VHDX differencing" section at :4190 and anything else matching differencing), `docs/resize.md`, plus 10c's and 10d's output, and answer one question: **is any fact about differencing stated differently on two pages?** Candidate facts to check explicitly — which ops refuse and with what exit code; whether `info` refuses (it does not); whether `map`'s message differs from the others (it does, per `docs/format-coverage.md:291`); whether a child must be the same format as its parent (`docs/create.md:269`); whether a fixed VHD can be a differencing parent; what `resize` does on a differencing VHD versus VHDX (`docs/resize.md:196` says the VHDX case is a known gap). For each disagreement, check the tree to find which page is right before changing either. Report anything that is a code defect rather than a documentation defect instead of fixing it. Do not rewrite prose that is already correct and measured — decision 7. Commit subject: `Reconcile the differencing documentation.` |
| 10f | low | sonnet | worktree | Changelog and close-out. Add an `Unreleased` entry under the existing `### Added` describing the differencing documentation as a whole, in the house style of the entries around it (bold lead sentence, then specifics). Confirm `docs/index.md` needs no change because no page was created — state that in the commit message rather than editing the file. Run `pre-commit run --all-files`, `make lint`, `make test-rust` and `tools/ci/check-doc-phrases.sh`; all must be clean. Commit subject: `Document the differencing documentation pass.` |

## Risks and mitigations

* **A sub-agent rewrites current, measured prose.** The largest risk
  in this phase, because four of six pages are already right and a
  brief that says "document differencing" invites a rewrite. Mitigated
  by decision 7 and by 10e's brief naming the specific facts to check
  rather than the pages to improve. The management session checks the
  diff for changes to lines carrying a "Measured" date and rejects any
  that are not accompanied by a fresh measurement.

* **10a reconstructs a plan filename wrongly.** "the PLAN-m workap"
  could plausibly be reconstructed as `PLAN-map` or `PLAN-m`. Mitigated
  by the brief requiring the file be confirmed to exist under
  `docs/plans/` before the name is written, and by 10b's guard, which
  catches a residual corruption but not a wrong-but-well-formed name.
  The management session greps each repaired name against
  `docs/plans/` directly.

* **Phase 10 documents a refusal phase 14 removes, and phase 16 misses
  a page.** Mitigated by decision 6: every statement of the refusal
  links `PLAN-differencing.md`, so phase 16 can find them with one
  grep. 10e records the list of pages carrying a refusal statement in
  its commit message, which is what phase 16 will work from.

* **The guard in 10b fires on a legitimate string.** A page could one
  day legitimately contain "PLAN-x workflow". The pattern
  `PLAN-[a-z] work[a-z]` requires a single letter between the hyphen
  and the space, which no real plan name has, so the false-positive
  surface is small. 10b's test plants a corrupted phrase and asserts
  the guard fires; it should also assert a legitimate `PLAN-differencing
  workflow` style string does not.

## Definition of done

* `git grep -nE 'PLAN-[a-z] work[a-z]'` returns nothing outside
  `docs/plans/` and `tools/ci/`. Those two are exempt and the
  criterion was wrong to omit them, which 10b found: the phase 9 and
  phase 10 plan files quote the corrupted phrases verbatim as examples
  of the bug, and the guard's own tests plant them on purpose, so a
  whole-tree form of this criterion can never be satisfied.
* `tools/ci/check-doc-phrases.sh` exits 0 on the tree, and exits
  non-zero naming the file and line when a corrupted phrase is planted.
  Demonstrated, not claimed, with the output in the commit message.
* `tools/ci/test-check-doc-phrases.sh` passes, and both it and the
  guard run in the `ci-tooling` job.
* Every plan filename written by 10a exists under `docs/plans/`,
  verified by resolving each name against the directory.
* `CHANGELOG.md` no longer claims differencing VHD is supported as an
  input with backing chains, and the correction is in the original
  entry rather than a deletion.
* `ARCHITECTURE.md` distinguishes a differencing child from a backing
  reference, in at most two added sentences, and gains no new section.
* `docs/guest-architecture.md` describes the differencing emit path and
  no longer frames refusal as the map operation's private limitation.
* No fact about differencing is stated differently on two pages: for
  each of the facts 10e's brief enumerates, every page that states it
  agrees. 10e's commit message lists the facts checked and the
  disagreements found.
* No line carrying a "Measured <date>" marker is changed without a
  fresh measurement recorded in the commit message.
* `docs/index.md` is unchanged, because no page was created.
* `docs/info.md` states the narrowed parent-path limitation and points
  at `docs/quirks.md` for the full account. It was added to scope
  during 10e: the stale claim lived on both pages, and fixing one
  would have left the two halves of one fact disagreeing.
* `make lint`, `make test-rust` and `pre-commit run --all-files` are
  clean.

## Back brief

Before 10e edits anything, it reports its findings list — the facts it
checked, which pages disagree, and which side it believes is right —
and waits. That pass is cheap to propose and expensive to redo: a
consistency edit made across four pages on a wrong reading of which
page is correct costs more to unpick than to agree up front. Every
other step proceeds without a gate.

10a and 10b are sequenced: the guard must be written against a
repaired tree, or its first run fails on 17 pre-existing hits.
