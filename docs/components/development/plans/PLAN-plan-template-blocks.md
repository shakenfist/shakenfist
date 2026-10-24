# Plan: shared blocks for PLAN-TEMPLATE.md, and a managed sub-agent model roster

## Context

This section describes the fleet as it stood in August 2026, when
the plan was written. Twelve repositories carry a template today;
see *Verification*.

Ten repositories carry a `PLAN-TEMPLATE.md`. Nothing audits any of
them, and they have drifted into three states:

| State | Repositories |
|-------|--------------|
| Full `## Agent guidance` section | shakenfist, ryll, kerbside, divergulent, instar |
| Truncated version of the same section | uncalibrated-sextant |
| No agent guidance at all | occystrap, client-python-k3s |

The six copies with agent guidance share an identical section
skeleton and near-identical prose. The differences are almost
entirely re-wrapping: `### Future work` says the same thing in all
six, wrapped to a different column in each. There is one real
semantic split -- divergulent says "the project's issue tracker
(once one exists)" where the others say "the relevant github bug
tracker".

The drift matters because the content is wrong in ways nobody
noticed:

* **No repository lists `fable`.** The rosters offer opus, sonnet
  and haiku only, so a planner has no way to recommend the most
  capable model for a step that needs it.
* **The context-window claim is stale.** Every copy says "opus has
  1M tokens, sonnet and haiku have 200K". Sonnet is 1M now. That
  paragraph actively steers planners towards opus for a reason that
  no longer holds.
* **The effort ladder stops at `high`.** `xhigh` and `max` exist
  and are the right settings for hard agentic work.

Each of these is a fleet-wide edit that today would mean editing
eight files in eight repositories by hand, which is exactly how the
drift happened in the first place.

## What "good" looks like

The mechanism already exists: `templates/shared-blocks/` plus the
version-marker discipline the `push-audit` check enforces. Editing
a canonical block and bumping its version marks every lagging
repository non-compliant on the next daily run and files the issues
automatically. Adding `fable` should be one file edit, one version
bump, one commit.

The organising rule for deciding what becomes a block: **a section
of the template is either wholly shared or wholly project-specific.**
Generic rules and local examples are not interleaved within a
section. Where a section is a generic rule plus a local example, the
rule is the block and the example follows it in an
`!!! note "In this project"` admonition.

The template itself is top-level and not published, so on GitHub
that admonition shows as literal `!!! note` text. That is the right
trade: nobody reads the template much, whereas master plans written
from it live in `docs/plans/` and *are* published through
mkdocs-material, where it renders properly. The material genuinely
does survive into those plans -- of shakenfist's 125 published
plans, 85 carry a Back brief, 76 carry Success criteria, and 44
carry Step-level guidance. A repeated `### In this project` heading
would render in all of them too, but would mint duplicate anchor
IDs and duplicate table-of-contents entries each time.

Only the three short runs that trail a shared block are marked.
Whole project-specific sections (`## Prompt`, `### Success
criteria`, `### Documentation index maintenance`) already announce
themselves by their headings, and the `...` placeholder sections are
specific to each plan rather than to the project.

That rule requires a restructure, not just markers. Today the
templates interleave at paragraph and bullet granularity --
`### Planning effort` opens with two generic sentences and then
talks about MariaDB accessors -- and `### Documentation index
maintenance` (project-specific) sits between `### Bugs fixed during
this work` and `### Back brief`, so the close-out sections are not
even contiguous.

## Execution

This plan was written before the Execution table convention, and
its phases are the prose sections below. The table was added when
the push audit was planned, on 2026-10-24, so the `Merged` cells of
phases 1 to 3 are reconstructed rather than recorded as each phase
landed; how is under *Push audit*.

| Phase | Status | Merged |
|-------|--------|--------|
| 1. Implementation: blocks, check and spec | Complete | `2468dda`, `5918f5b` direct; `5b1fb74` (#49) and `ff92357` (#50) are recorded and audited under `PLAN-push-audit-phase` |
| 2. Migration | Complete | shakenfist `0f21df0` (#3785), ryll `1a7b47e` (#281), kerbside `b29e01f` (#327), divergulent `70b2a8f` (#67), instar `cbcdd2a` (#500), uncalibrated-sextant `d81eb69` (#5), occystrap `b1438e9` (#115), client-python-k3s `12dbacd` (#30) |
| 3. Verification | Complete | n/a -- confirmations against scratch copies, no commit |
| 4. Push audit | Complete | `1fc93d7` (#312) |

## Implementation

The sections below describe the implementation as it landed, in
August 2026. `PLAN-audit-scripts-restructure` has since moved the
code: `check_plan_template` is now the `PlanTemplate` class in
`scripts/audit/checks/plans.py`, registered in
`scripts/audit/registry.py`, the spec is
`docs/audits/plan-template.md`, and `PLAN_TEMPLATE_BLOCKS` names
ten blocks since `plan-phase-landing` joined it on 2026-09-19.

### 1. Nine shared blocks -- `templates/shared-blocks/`

| Block | Covers | Churn driver |
|-------|--------|--------------|
| `plan-file-conventions` | where plans live, phase file naming, the phase tracking table, one commit per logical change | rare |
| `plan-status-vocabulary` | the fixed set of terms a status cell may hold | rare |
| `subagent-execution-model` | implementation happens in sub-agents; the plan/spawn/review/retry/commit loop; worktree isolation | rare |
| `plan-planning-effort` | master plan at high effort; each phase states its planning effort | rare |
| `subagent-step-guidance` | the step table, the effort ladder, what makes a good brief | rare |
| `subagent-model-roster` | which models exist, what each is for, context windows, skew-heavier rule | every model launch |
| `plan-review-checklist` | what the management session verifies after a sub-agent completes | rare |
| `plan-closeout-sections` | Future work, Bugs fixed, Back brief -- headings included | rare |
| `plan-push-audit-phase` | every master plan ends with a phase running `PUSH-AUDIT.md` over the whole plan's work | rare |

This started as seven. `plan-status-vocabulary` was added when the
`plan-index` audit needed the same wording on both sides, and
`plan-push-audit-phase` by `PLAN-push-audit-phase.md`, which needed
somewhere to put the pre-push audit's trigger and found that this
plan's migration was the only pass that would carry it into eight
templates without editing them twice. The migration below carried
the seven that existed on 2026-08-16; the other two reached the
templates afterwards through the issues the audit files.

`subagent-model-roster` is separate from `subagent-step-guidance`
on purpose. It churns whenever a model ships or retires while the
effort ladder sits still for months, and separating them means the
auto-filed issue says the model roster is stale rather than that
step guidance is stale. Verified: bumping the roster to v2 produces
`shared block subagent-model-roster is stale (v1 embedded, v2
current)` and nothing else.

`plan-closeout-sections` includes its own `###` headings and the
`...` placeholders beneath them, because those are sections the plan
author fills in rather than instructions to read. That is safe
because real plans in `docs/plans/` adapt the template rather than
copying it byte-for-byte -- checked against shakenfist's existing
plans, which diverge substantially from the template's headings.

Content corrections folded into the blocks: `fable` added to the
roster with guidance to reserve it for steps that have defeated
opus; the context-window sentence corrected (fable, opus and sonnet
1M, haiku 200K); the effort ladder extended through `xhigh` and
`max`; and divergulent's better "the project's issue tracker, where
one exists" wording adopted fleet-wide over "the relevant github
bug tracker".

### 2. Automated check -- `scripts/audit-check.py`

`check_plan_template` mirrors `check_push_audit`: repositories with
no `PLAN-TEMPLATE.md` are `not_applicable`; those that have one must
carry all nine blocks, current and verbatim. Registered in
`check_calls()` and in `audit_common.py` (`AUDIT_METADATA` and
`ISSUE_TITLES`).

Whether every repository should have a plan template is deliberately
left as a separate decision. Eight of the sixteen audited
repositories have none, and a template's project-specific half has
to be written with real knowledge of the project -- an empty one is
worse than none.

### 3. Audit spec -- `audits/plan-template.md`

Documents what is checked, which blocks are required, why the model
roster is separate, and what stays project-specific. Registered in
`audits/README.md` and `PROJECT-CONSISTENCY-AUDITS.md`.

## Migration (separate commits, one per repository)

Landed in all eight repositories, each as one pull request merged
on 2026-08-16 and touching `PLAN-TEMPLATE.md` alone; the record is
under *Push audit* below. Those carried the restructure and the
seven blocks that existed that day. `plan-status-vocabulary`
(2026-08-19) and `plan-push-audit-phase` (2026-08-24) reached the
templates afterwards through the issues the audit files, which is
the steady state working rather than more migration.

An earlier revision of this paragraph said five had landed and
occystrap was outstanding, which was never true -- occystrap#115
merged the same day as the rest -- and that shakenfist was
non-compliant over `plan-push-audit-phase` (shakenfist#3892), which
shakenfist#4160 fixed and the audit closed on 2026-09-13. Where each
repository stands today is under *Verification*. Each migration
followed these steps:

1. Restructure `PLAN-TEMPLATE.md` so no section mixes generic and
   project-specific text. In practice: move the project-specific
   examples out of `### Planning effort` and `### Step-level
   guidance` to sit *after* the block, wrapped in an
   `!!! note "In this project"` admonition; promote the model
   roster to its own `### Model choice` heading; move
   `### Documentation index maintenance` up next to
   `### Success criteria` so the close-out sections become
   contiguous.
2. Embed every block that exists at the time, verbatim.
3. Confirm `audit-check.py` reports `plan-template` as pass.

occystrap and client-python-k3s have no `## Agent guidance` section
at all, so they gain the whole sub-agent workflow rather than having
it rewritten.

A reference restructure of shakenfist's template was assembled and
verified to pass, then reverted -- the canonical wording needs
review before eight copies of it exist.

## Verification

* `check_plan_template` returns `not_applicable` for clingwrap (no
  template) and `fail` with per-block detail for shakenfist
  (template present, no blocks yet). Confirmed.
* A restructured shakenfist template carrying all seven blocks
  returns `pass`. Confirmed against a scratch copy -- at seven,
  before `plan-status-vocabulary` and `plan-push-audit-phase` were
  added. Re-confirmed on 2026-10-24 at ten blocks, after
  `plan-phase-landing` joined `PLAN_TEMPLATE_BLOCKS`: run against
  fresh clones of all eleven other repositories carrying a
  template. Of those, andris, kerbside-patches, occystrap,
  private-ci and shakenfist pass, as does this repository; the other
  six are covered by the next two bullets.
* Bumping a block version marks the embedding repository stale, and
  names the block that moved. Confirmed, and since confirmed in the
  field twice: the `plan-file-conventions` v2 bump (`d0ab9d4`,
  2026-10-06) and the `plan-push-audit-phase` v4 bump (`3d35d34`,
  2026-10-13) each produced `shared block <name> is stale (vN
  embedded, vM current)` naming that block and nothing else.
  client-python-k3s, divergulent, instar, kerbside and ryll fail on
  2026-10-24 on exactly those lines and no others. An earlier
  revision of this bullet dated the v4 bump to `d502250`, which
  reworded the block a day later and left it at v4. No repository
  reports that block as drifted on 2026-10-24.
* uncalibrated-sextant is the one repository missing blocks rather
  than lagging a version: `plan-status-vocabulary` and
  `plan-push-audit-phase`, both written after its migration, and
  `plan-phase-landing`, which is not this plan's. That is
  uncalibrated-sextant#12, open since 2026-09-04 in a repository
  with no push since 2026-09-03. It is consistency-fix work rather
  than migration; see *Future work* under *Push audit*.

## Push audit

This is the last phase of the plan and it is not optional. It comes
after *Migration* and *Verification* because those are where the
bulk of the work is: a push audit run at the end of *Implementation*
would cover none of the eight repositories the migration touches.

There are two obligations here and they are not the same one:

* **Each migration pull request** runs its own repository's
  `PUSH-AUDIT.md` over that pull request, against that repository's
  default branch, as part of that pull request. That is a per-repo
  obligation discharged eight times.
* **This plan's final phase** runs this repository's
  `PUSH-AUDIT.md` over the accumulated diff of the whole plan --
  the shared blocks, `check_plan_template` and the audit spec --
  against `main`, not over the last commit alone. It runs once the
  migration is complete.

The blocks half of that diff did not land in one place, so the range
is recorded here rather than derived when the phase runs.
`plan-push-audit-phase` asks for a `Merged:` line per phase where a
plan's phases are prose sections rather than a table; this plan's
implementation sections were built in four landings, so the line
names the set:

Merged: `2468dda` and `5918f5b` direct to `main`; `5b1fb74` (#49)
and `ff92357` (#50). Between them those cover all nine blocks,
`check_plan_template`, and `docs/audits/plan-template.md` --
`2468dda` carried seven blocks plus the check and the spec page,
`5918f5b` added `plan-status-vocabulary`, and #49 and #50 added and
then revised `plan-push-audit-phase`. The eight migration pull
requests are listed in the *Landing record* below. None of them ran a
push audit, because the obligation postdates them, and step 4b runs
the floor checklist over them instead. An earlier revision of this
paragraph said the migration was in four other repositories and was
audited there; neither was true.

**Two corrections to that line, both worth reading before doing this
for another plan.** It first read as seven commits "all direct to
`main`", derived from `git log -- templates/shared-blocks/`. That
was wrong twice over. Two of the seven were not direct: `51d872f`
arrived inside #21 and `e2585e3` inside #49, so anchoring on either
would have audited one commit of a pull request rather than the pull
request, and `e2585e3` was double-counted against `5b1fb74` besides.
A path-filtered log does not distinguish the two cases;
`git rev-list --first-parent` does, and is now what the block tells
you to use.

The larger error was scope. Four of the seven -- `9a74046`
(`readme-discipline`), `fbcd759` (`comment-proportion`), `3abb973`
(`plan-phase-references`) and `51d872f` (`llm-doc-discipline`) --
are not this plan's work at all. They touch
`templates/shared-blocks/` but none of them is one of this plan's
nine blocks, so an audit anchored on them
would have swept in four unrelated consistency audits. Recording a
range by the directory a plan happens to touch is the same mistake
as deriving it from a date: both answer "what else was going on"
rather than "what did this plan do".

Findings from either land as their own pull request; the plan is not
complete until each is resolved or declined in writing, with the
reason recorded here. If an audit finds nothing, say so in one
sentence.

### Planning effort

High effort. The work is mechanical once scoped, but
every decision below is about what the audit reads, and the two
previous attempts to record this plan's range (above) both went
wrong on exactly that.

### Scope

In: wave 1 and wave 2 of this repository's `PUSH-AUDIT.md` over
this plan's two direct landings and the code that succeeds them; the
push-audit floor checklist over the eight migration pull requests;
the triage and outcome record; and closing the plan out. The
subject widened during execution to the whole of `PlanIndex`,
`docs/audits/plan-index.md` and their tests, once wave 1 showed
that `5918f5b` created that criterion; see *Execution notes*.

Out: `5b1fb74` (#49) and `ff92357` (#50), already audited (decision
2); `plan-phase-landing`, the v2 and v4 bumps and every
consistency-fix re-sync since the migration, which are other plans'
work or the steady state (decision 5); and fixing
uncalibrated-sextant#12 (decision 6). Findings are fixed in their
own pull request, not on this branch.

### What the survey found

The survey ran on 2026-10-24 against `main` at `aa8d259` and fresh
clones of the eleven other repositories that carry a
`PLAN-TEMPLATE.md`. The false claims it found have been corrected
at their source -- in *Migration*, *Verification*, the new
*Execution* table, the note at the head of *Implementation*, and
the plan's row in `docs/plans/index.md` -- so they are not repeated
as work below.

* **The migration was complete, not outstanding.** All eight
  repositories merged a migration pull request on 2026-08-16, each
  touching `PLAN-TEMPLATE.md` only. occystrap's (#115) was among
  them. shakenfist#3892 closed on 2026-09-13.
* **None of the eight ran a push audit.** No body or comment on any
  of them mentions one, which is expected: the per-repository
  obligation arrived with `plan-push-audit-phase` on 2026-08-24,
  eight days later.
* **The implementation's code no longer exists where this plan
  says.** `check_plan_template` in `scripts/audit-check.py` is now
  `PlanTemplate` at `scripts/audit/checks/plans.py:1672`; the block
  list is `PLAN_TEMPLATE_BLOCKS` at `plans.py:1081`; the validator
  is `validate_shared_blocks` at
  `scripts/audit/text/shared_blocks.py:84`; the spec moved from
  `audits/` to `docs/audits/plan-template.md`; and `AUDIT_METADATA`
  and `ISSUE_TITLES` are now derived from the registry, with the
  titles frozen at `scripts/tests/test_metadata.py:359` and `:362`.
  `PLAN-audit-scripts-restructure` did that move and is `Complete`.
* **The block list is ten, not nine.** `plan-phase-landing` was
  added on 2026-09-19 (`9bfc0e8`), by `PLAN-review-unit-size`.
* **#49 and #50 are already audited.** They are phases 1 and 2 of
  `PLAN-push-audit-phase`, whose own push audit read both
  (`PLAN-push-audit-phase.md:239-240` and its decision 1).
* **Fleet state on 2026-10-24**, from `audit-check.py` against the
  clones: andris, development, kerbside-patches, occystrap,
  private-ci and shakenfist pass `plan-template`; client-python-k3s,
  instar, kerbside and ryll fail only on `plan-push-audit-phase`
  v3-of-v4; divergulent fails on that and `plan-file-conventions`
  v1-of-v2; uncalibrated-sextant is missing three blocks
  (uncalibrated-sextant#12). The weekly `docs/audits/compliance.md`
  lags this -- it still shows occystrap non-compliant over #157,
  which closed on 2026-10-17.
* **development's own template marks its project-specific runs
  with a bold `**In this repository.**` paragraph**, where the
  other eleven use the `!!! note "In this project"` admonition that
  this plan and `docs/audits/plan-template.md:80` prescribe.
  development publishes nothing through mkdocs, so the rendering
  argument for the admonition does not reach it; whether the spec
  should say so is a question for 4e.

### Decisions

1. **The phase lives in this section, not a phase file.** This
   repository keeps phases inline (`docs/plans/index.md`, and the
   note under `plan-file-conventions` in `PLAN-TEMPLATE.md`), and a
   seven-step audit is not large enough to argue with that.
2. **#49 and #50 are cited, not re-audited.** `PLAN-push-audit-phase`
   ran this repository's runbook over both. Running it again would
   spend four wave 2 agents re-reading `plan-push-audit-phase` and
   `plan-audit-phase`, which are that plan's work rather than this
   one's. The interaction the accumulated-diff rule exists to catch
   -- what #49 and #50 did to the blocks and check that `2468dda`
   created -- is covered by decision 3, because the code wave 2
   reads is what all four landings and everything since left
   behind.
3. **Wave 1 reads the historical diffs; wave 2 reads the code that
   succeeds them, at `HEAD`.** `2468dda` and `5918f5b` added code to
   a `scripts/audit-check.py` that no longer exists in that shape,
   and a spec under an `audits/` directory that has moved. A code
   review of deleted functions finds nothing anyone can fix. The
   historical diffs still matter for the questions wave 1 asks --
   was a version marker moved with its wording, was an issue title
   introduced that the frozen table now has to keep, did anything
   private enter the repository -- so wave 1 runs over them as
   recorded, and wave 2 gets them as context and the live files as
   its subject. This is the decision a reviewer is most likely to
   dispute, because it means no agent reviews the original
   `check_plan_template` as written. The answer is that
   `PLAN-audit-scripts-restructure` already reviewed its translation
   into the class, and the class is what runs every morning.
4. **The eight migration pull requests get the floor checklist,
   not eight runbooks.** They predate the per-repository obligation,
   each changed one prose file, and every one of those files has
   since been rewritten by at least one re-sync, so a
   retrospective `PUSH-AUDIT.md` run per repository would audit
   text that no longer exists, through eight runbooks written for
   other blast radii. The floor -- items 1, 3 and 4 of the list in
   `plan-push-audit-phase` -- asks the questions that still have
   consequences: is the record well-formed, did anything secret or
   private enter a public repository, and did a removed heading
   leave a consumer behind. Items 2 and 5 do not apply to a prose
   file and 4b says so.
5. **Scope is this plan's blocks and this plan's commits.** The
   *Push audit* text above already learned that a range drawn by
   directory sweeps in other plans' work. The same holds for later
   versions of these blocks: a bump to `plan-file-conventions` v2
   belongs to whoever made it.
6. **uncalibrated-sextant#12 does not hold the plan open.** The
   migration landed there (#5) with every block that then existed.
   What is missing is two blocks written afterwards and one from
   another plan, the issue the mechanism exists to file has been
   filed, and fixing it is consistency-fix work in a repository
   nobody has pushed to since September. It goes in *Future work*.
7. **Close-out follows `plan-phase-landing`.** If the audit raises
   findings, their pull request carries the close-out: row 4 to
   `Complete`, with the merge commit of the pull request that lands
   this record as its `Merged` cell, and the index row to `Complete`.
   The findings pull request's own merge is not recorded in row 4; it
   is cited in the *Outcome*. If it raises none, this phase's own pull
   request sets both and row 4 records no `Merged` cell.

### Landing record

Reconstructed on 2026-10-24 from `gh pr view` and `git cat-file`,
not from a path-filtered log. Each direct landing is one commit
with one parent, read as `<sha>^..<sha>`; the two are not
contiguous, so there is no single range.

| Landing | Form | Diff | Audit |
|---------|------|------|-------|
| `2468dda` | direct, 2026-08-16 | +628 -4, 14 files | this phase, 4a and wave 2 |
| `5918f5b` | direct, 2026-08-19 | +696 -4, 9 files | this phase, 4a and wave 2 |
| `5b1fb74` (#49) | merge | | `PLAN-push-audit-phase` phase 5 |
| `ff92357` (#50) | merge | | `PLAN-push-audit-phase` phase 5 |
| shakenfist `0f21df0` (#3785) | merge | +230 -167, 1 file | none ran; 4b |
| ryll `1a7b47e` (#281) | merge | +225 -168, 1 file | none ran; 4b |
| kerbside `b29e01f` (#327) | merge | +235 -174, 1 file | none ran; 4b |
| divergulent `70b2a8f` (#67) | merge | +236 -166, 1 file | none ran; 4b |
| instar `cbcdd2a` (#500) | merge | +243 -181, 1 file | none ran; 4b |
| uncalibrated-sextant `d81eb69` (#5) | merge | +223 -124, 1 file | none ran; 4b |
| occystrap `b1438e9` (#115) | merge | +250 -29, 1 file | none ran; 4b |
| client-python-k3s `12dbacd` (#30) | merge | +263 -34, 1 file | none ran; 4b |

The two direct landings touch 17 distinct paths between them.

### Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | Wave 1 of `PUSH-AUDIT.md`, run twice: `AUDIT_RANGE=2468dda^..2468dda`, then `AUDIT_RANGE=5918f5b^..5918f5b`. `git fetch origin` first. Run `pre-commit run --all-files` once, on a clean tree (`git status --short` empty), because `review-tracking.py stamp` reads SHAs from the index. Before the greps for each range run `git diff --numstat "$AUDIT_RANGE" \| awk '{i+=$1; d+=$2} END {print i, d, NR}'`: expect `628 4 14` and `696 4 9`, and a union of 17 paths from `for r in 2468dda^..2468dda 5918f5b^..5918f5b; do git diff --numstat $r; done \| cut -f3 \| sort -u \| wc -l`. Anything else means the wrong range: stop and report rather than auditing it. These commits predate `PLAN-audit-scripts-restructure`, so several greps name paths that did not exist yet (`scripts/tests/test_metadata.py`, `docs/audits/`) and will be empty by construction. Say so per grep, and run the question the grep was asking against the paths that did exist: for the issue-title check, read what each commit added to `ISSUE_TITLES` in `scripts/audit_common.py` and confirm the titles (`Plan template`, `Plan index`) match `FROZEN_ISSUE_TITLES` at `scripts/tests/test_metadata.py:359` and `:362` today; for the shared-block check, `2468dda` creates seven blocks at v1, which is not an edit, and `5918f5b` creates `plan-status-vocabulary` and edits `templates/shared-blocks/README.md` and `audits/plan-template.md`, neither of which carries a marker -- confirm both rather than assuming. Report every hit with a verdict (looked at, accepted or blocking, and why) and name the checks that were empty in both ranges. Report, do not fix. No commit. |
| 4b | medium | sonnet | none | Push-audit floor over the eight migration pull requests in the *Landing record* table, read with `gh pr diff <n> -R shakenfist/<repo>` -- no clone needed, and do not push or comment anywhere. For each: (1) confirm `gh pr view <n> -R shakenfist/<repo> --json mergeCommit,additions,deletions,changedFiles` matches the table row; (3) grep the added lines for anything resembling a token or key, a private hostname (`*.home.stillhq.com`, `*.local`, an internal-only name) or an RFC 1918 address, and report each with a verdict -- these are public repositories; (4) list every `#`, `##` and `###` heading the diff removed or renamed, then grep that repository's current default branch (`gh api repos/shakenfist/<repo>/contents/...` or a shallow clone into the scratchpad) for references to each, including AGENTS.md, docs/ and `.claude/skills/`, and also grep shakenfist/development's `scripts/` and `templates/` for any that the audit tooling parses by name. State the greps you ran. Items 2 (unattended jobs) and 5 (publishing) do not apply to a prose file: say so in one sentence each. Report, do not fix. No commit. |
| 4c | high | sonnet | none | Wave 2a, code quality, per the 2a brief in `PUSH-AUDIT.md`. Subject: the live code at `HEAD`, not the historical diff -- `PlanTemplate` (`scripts/audit/checks/plans.py:1672-1710`), `PLAN_TEMPLATE_BLOCKS` (`plans.py:1081`) and its comment, `load_canonical_block` and `validate_shared_blocks` (`scripts/audit/text/shared_blocks.py:67` onward), and the status-vocabulary path of `PlanIndex` (`plans.py:1256` onward). Read `git show 2468dda` and `git show 5918f5b` (+628 -4 and +696 -4; state those totals in your report) for what the code was meant to do. Questions to answer: does the validator distinguish a stale version, an edited body at the current version, a missing end marker, a block embedded twice and a block nested inside another, and does each produce a detail string that tells a maintainer in another repository what to do; is `PLAN_TEMPLATE_BLOCKS` the only list of these names, or does a second copy exist in `docs/audits/plan-template.md`, `templates/shared-blocks/README.md` or elsewhere that could drift from it; and does the comment above it still describe the list. Apply the `source-file-size` judgement to `plans.py` (1,710 lines). Report, do not fix. No commit. |
| 4d | high | sonnet | none | Wave 2b, test review, per the 2b brief in `PUSH-AUDIT.md`, over the same live subject as 4c. Read `PlanTemplateTest` (`scripts/tests/test_plans.py:3162` onward), the `PlanIndexTest` vocabulary cases (`test_plans.py:826` onward), and the validator tests in `scripts/tests/test_containment.py`. For each behaviour 4c's brief lists -- stale version, edited body, missing end marker, duplicate, nested, not-applicable when there is no template, and every name in `PLAN_TEMPLATE_BLOCKS` having a canonical file -- say whether a test exists and whether it would fail if the behaviour broke, naming the test. A test that asserts only that `fail` was returned, not which block the detail names, does not prove the roster-separation claim in *Implementation*. Report, do not fix. No commit. |
| 4e | high | opus | none | Wave 2c, documentation review, per the 2c brief in `PUSH-AUDIT.md`. Read the ten canonical blocks in `templates/shared-blocks/` named in `PLAN_TEMPLATE_BLOCKS` together, in the order `PLAN-TEMPLATE.md` embeds them, as the one document a planner in another repository reads, and report any two that contradict each other -- the likeliest place is the `Merged` column, which `plan-file-conventions`, `plan-push-audit-phase` and `plan-phase-landing` all describe. Check `subagent-model-roster` against the models actually available today: report any model it omits or names wrongly and any context-window figure you cannot confirm, saying which you could not confirm rather than guessing. Then check `docs/audits/plan-template.md`, `templates/shared-blocks/README.md` and `docs/audits/README.md:229` against the code -- block count, block names, and what a maintainer is told to do -- and judge whether `docs/audits/plan-template.md:80` should acknowledge that a repository which publishes nothing through mkdocs (this one) may mark its project-specific runs another way. Do not edit any file under `templates/shared-blocks/`: a version bump files issues in eleven repositories, so a finding there is reported with the bump it would need. Report, do not fix. No commit. |
| 4f | high | opus | none | Wave 2d, security review, per the 2d brief in `PUSH-AUDIT.md`, over the same live subject as 4c, with the historical diffs as context. The blast radius is other repositories' issue trackers: `validate_shared_blocks` reads `PLAN-TEMPLATE.md` from repositories this one does not control, and its detail strings go into issue bodies filed and rewritten by `audit-manage-issues.py`. Establish whether any part of a detail string is taken from the scanned file rather than from `PLAN_TEMPLATE_BLOCKS` -- a block name or version read from a crafted marker -- and if so whether it can carry an `@` mention, a closing keyword, a link or markdown that changes the issue body. Establish whether `load_canonical_block` can ever be called with a name from scanned content, and if so whether `../` escapes `templates/shared-blocks/`. Consider what a very large or pathological template does to the marker regex. Apply the `path-traversal-review` shared block. Report, do not fix. No commit. |
| 4g | high | opus | none | Management triage, in the session rather than a sub-agent. First check each report states the totals it read, and that 4a's match the *Landing record*; re-run any that do not. Decide each finding blocking, advisory or declined, and write an **Outcome** subsection here: what 4a and 4b found, what each wave 2 agent found, and for every finding where it was fixed or why it was declined. If all are clean, one sentence saying so. Work the `PUSH-AUDIT.md` management checklist. Fixes land as their own pull request against `main`. Close out per decision 7. Commit subject: "Record the plan template blocks push audit." |

### Risks and mitigations

* **An empty diff read as a clean audit.** Already-merged work
  under the runbook's `origin/main...HEAD` default diffs nothing.
  Mitigation: every brief carries its concrete range, every report
  states its totals, and 4g refuses a report whose totals do not
  match the table.
* **Wave 1 greps that cannot fire, reported as clean.** Several
  greps name paths that postdate these commits. Mitigation: 4a must
  say per grep whether it could have fired, and run the underlying
  question against the paths that existed; 4g checks that it did.
* **Decision 3 misses a defect that existed only in the original
  code.** If it was translated faithfully it is in the class and
  wave 2 sees it; if it was not, the restructure fixed it.
  Mitigation: 4c reads both commits for intent, and a behaviour the
  original promised that the class does not deliver is itself a
  finding.
* **A fix to a canonical block files issues fleet-wide.**
  Mitigation: no step edits `templates/shared-blocks/`; 4g decides
  any bump deliberately, in the findings pull request, and states
  how many repositories it newly fails.

### Definition of done

* Every report from 4a to 4f states the totals it read, and 4a's
  are `628 4 14` and `696 4 9` with a 17-path union. Reproducible
  with:

  ```
  for r in 2468dda^..2468dda 5918f5b^..5918f5b; do
      git diff --numstat $r | awk '{i+=$1; d+=$2} END {print i, d, NR}'
  done
  for r in 2468dda^..2468dda 5918f5b^..5918f5b; do
      git diff --numstat $r; done | cut -f3 | sort -u | wc -l
  ```

* This section has an **Outcome** subsection giving a verdict for
  every finding, or one sentence saying there were none.
* Every row of the *Landing record* matches `gh pr view` or
  `git cat-file -p`, and 4b's report covers all eight pull
  requests.
* Row 4 of *Execution* and the plan's row in `docs/plans/index.md`
  are `Complete`, set as decision 7 says and not before.
* `python3 scripts/audit-check.py --repo-path . --repo-name
  development` reports every `plan-*` check as it did on
  2026-10-24 (pass, or `not_applicable` for
  `plan-source-references`).
* `pre-commit run --all-files` passes.

### Future work

* uncalibrated-sextant#12: three blocks missing from its
  `PLAN-TEMPLATE.md`, and four other consistency issues open there
  since September. consistency-fix work, with no owner while the
  repository is dormant.
* `docs/audits/compliance.md` is regenerated weekly and lags the
  daily issue state, so it showed occystrap non-compliant a week
  after occystrap#157 closed. Worth knowing before trusting it as a
  survey source; not this plan's to fix.

### Back brief

Before running 4a, back brief the operator: which ranges each step
reads, why #49 and #50 are cited rather than re-run, and why wave 2
reads the live code rather than the historical diff. Decisions 2, 3
and 4 narrow the audit, and a narrowing is cheap to agree before the
agents run and expensive to discover afterwards.

### Execution notes

Run on 2026-10-24 between 02:20 and 02:50 UTC, from the management
session, with the back brief agreed as written: decisions 2, 3 and 4
stand, and this session may push and open the pull request.

* 4a and 4b ran first, in parallel, both read-only. 4c to 4f ran in
  parallel once wave 1 had passed, as `PUSH-AUDIT.md` asks. 4g is
  this record.
* The survey undercounted `5918f5b`. It did not only create
  `plan-status-vocabulary`: it created the whole `plan-index`
  criterion, with its check, `audits/plan-index.md` and 263 lines of
  tests. 4a found this; 4c to 4f were told, and `PlanIndex` was
  reviewed in full rather than only its vocabulary path. The 4a brief
  above is left as it ran.
* The GitHub core API quota ran out during 4b, so `gh pr diff`
  failed for all eight pull requests. 4b took the diffs from
  blobless bare clones instead (`git diff <merge>^1 <merge>`), and
  the `gh pr view` figures it checked against had already been
  fetched. The issues below were filed after the quota reset.
* Every report stated its totals, and all matched the *Landing
  record*: `628 4 14` and `696 4 9`, 17 paths in the union, and the
  eight pull requests' figures. No report was re-run.

### Outcome

Line references in this section, the survey and the step plan are as
of `aa8d259`, the `main` this phase branched from; the findings pull
request will move many of them.

Nothing blocking. Wave 1 passed. The floor checklist over the eight
migration pull requests found nothing. Wave 2 raised one
medium-severity defect and a set of advisory ones, most of them in
the shared-block validator that `2468dda` pointed at
`PLAN-TEMPLATE.md`. The fixes are listed below and land as one
findings pull request against `main`. That pull request also carries
the close-out under decision 7. Blocking here would mean holding a
push, and the code under audit has been on `main` since August, so
severity decides the order of the fixes, not whether this record
merges. Problems that predate this plan, or that belong to another
plan's blocks, are filed as issues rather than treated as findings.

**4a, wave 1.** `pre-commit run --all-files` passed. Every grep was
either empty or a hit that was looked at and accepted:

* Seven blocks created at v1 by `2468dda`, and
  `plan-status-vocabulary` at v1 by `5918f5b`. Neither commit edits
  an existing block.
* `+import re`, which is stdlib.
* The empty `consistency-audit` anchor pair in each new spec.
* `PLAN-TEMPLATE.md` named as a file rather than as a plan.

The titles `Plan template` and `Plan index` match
`FROZEN_ISSUE_TITLES` today. `scripts/audit-check.py` was 3,276 and
then 3,658 lines after these commits, which is well past the size
mark. The restructure has since split it, so that is history rather
than a finding. One defect in the runbook itself:
`PUSH-AUDIT.md:150` puts `--name-only` after the `--`, so git reads
it as a path and prints the whole diff. It predates this plan, and is
issue development#309.

**4b, migration floor.** All eight pull requests match the *Landing
record*.

* No secret, private hostname or address was added.
* The only removed headings are `### Future work` and `### Bugs
  fixed during this work`, in six templates. Both are re-added in the
  same diff below the new `### Model choice`. Both are present on
  every default branch today, so no consumer was stranded.
* Items 2 and 5 of the floor checklist do not apply to a prose file.

**Fixed in development#317**, the findings pull request, which
closes development#313. Findings 1 to 6 are `d0e26ce`, 7 to 9 are
`a6af4a5` and 10 is `f66c59c`; the descriptions below are as the
audit found them. Every finding is fixed, with one part of finding 7
declined: a subdirectory link such as `completed/PLAN-a.md` still
registers by basename, because repositories file finished plans under
`docs/plans/completed/` and `plan_index_entries` has always read them
that way. The fixes moved no `plan-template`, `push-audit`,
`plan-index` or `plan-audit-phase` verdict for any of the twelve
repositories carrying a template, measured against fresh clones. Two
fixes went wider than the finding:

* The unreadable-file fix applies to `PushAudit` as well as
  `PlanTemplate`. The same pattern there also let an unreadable
  `PUSH-AUDIT.md` fall through to auditing `PUSH-TEMPLATE.md`.
* `plan_index_entries` had the same blind spot for titled and
  angle-bracketed links, and both readers now share one helper.

1. **The block extractor uses memory that grows with the square of its
   input.** This is medium severity, from 4f, reproduced here.
   `extract_shared_blocks` (`scripts/audit/text/shared_blocks.py:54`)
   slices from every begin marker to the next end marker and keeps
   every slice, and runs one `find` per begin marker, so time grows
   with the square of the input as well as memory. A file of 200 KB of
   begin markers and one end marker took 428 MB, and the read cap
   allows 2 MB, on a shared self-hosted runner. The extractor predates
   this plan (`9a74046`). It is fixed here anyway: `2468dda` gave it
   its largest untrusted input, and the same rewrite closes the next
   three items. The fix is a single linear pass that pairs begin and
   end markers in order, with a test on the pathological input that
   bounds both.
2. **The validator's account of malformed blocks is wrong.** From
   4c, 4d and 4e.
   * A block missing its end marker borrows the next block's marker
     and is reported as `drifted`. That contradicts
     `docs/audits/plan-template.md:111`.
   * A block nested inside another is reported the same way.
   * A block embedded twice passes silently. Reproduced here: two
     copies of `plan-file-conventions` return no problems.
   * A version marker of more than 4,300 digits raises `ValueError`
     and turns the check into an `error`, which freezes the issue
     and skips the fleet's compliance update.

   The rewrite pairs each begin marker with its own end marker,
   reports nesting, duplicates and a missing end marker by name, and
   bounds the version.
3. **Smaller validator fixes.**
   * `stale` and `drifted` should tell a maintainer in another
     repository where to copy the block from, as `missing` already
     does.
   * `load_canonical_block` should open its file as UTF-8.
   * `load_canonical_block` should say in a comment why its bare
     `os.path.join` is safe: the name regex admits no `/` or `.`.
     The `path-traversal-review` block asks for exactly that comment.
4. **The roster-separation claim in *Implementation* has no test.**
   From 4d, by mutation: a validator that marks every block stale
   when one is stale passes the suite. Assert that a stale block is
   the only block named, and add a case that stales
   `subagent-model-roster` alone.
5. **`PLAN_TEMPLATE_BLOCKS` is pinned for three of its ten names.**
   From 4d: dropping `subagent-model-roster`,
   `plan-closeout-sections` or `plan-file-conventions` survives,
   because the fixtures are built from the list. Pin the list
   literally. That change is the one that re-marks the fleet, which
   is what the test class's docstring says it is for.
6. **Validator behaviours are tested only through `PushAuditTest`.**
   From 4d: a drifted body, a missing end marker and an unknown
   block have no test through `PlanTemplate`. Duplicate and nested
   blocks have no test at all. Add them with item 2.
7. **`PlanIndex` registers a local plan from any link that ends in
   its filename.** From 4c, reproduced here. `[a](/components/development/plans/sub/PLAN-a/)`
   and an absolute URL to another repository's `PLAN-a.md` both
   satisfy the row for a local `PLAN-a.md` (`plans.py:1320`). A
   titled link, `[a](PLAN-a.md "t")`, and an angle-bracketed one are
   not recognised at all.
8. **Two `PlanIndex` guards survive mutation.** From 4d:
   `PLAN_INDEX_MAX_SHOWN` and the `repo.isfile` filter on master
   plans. The second is the earlier symlink-escape fix, so a
   surviving mutant there is worth a test. Two `assert_skip` calls
   also lack `containing`, so they pass on either skip message.
9. **An unreadable `PLAN-TEMPLATE.md` is reported as absent.** From
   4c: a template over the read cap, or a symlink out of the clone,
   gives `No PLAN-TEMPLATE.md`, and `not_applicable` then closes an
   open issue. Report it as unreadable.
10. **Spec corrections.**
    * `docs/audits/plan-index.md:73` says the criterion has no
      template, but `PlanIndex.template` names
      `templates/shared-blocks/`, so every `plan-index` issue links
      one. Correct the spec.
    * `docs/audits/plan-template.md:80` should say that the
      admonition is for repositories whose plans publish through
      mkdocs. Elsewhere any non-heading marker will do, and the check
      does not test for either.
    * `docs/audits/plan-template.md:35-48` describes
      `plan-push-audit-phase` "since v2" and is silent on v3 and v4.
    * The module docstring of `plans.py` justifies its 1,710 lines by
      a validator that has since moved to `shared_blocks.py`. Restate
      the reason, or name the seam (`PushAudit`, `PlanTemplate` and
      their block lists).

**Filed as issues:**

* development#309: `PUSH-AUDIT.md:150`'s `--name-only` (4a), and
  `PLAN-TEMPLATE.md:230-233`, which still says every diff command is
  written against `main...HEAD` (4e).
* development#310: three places where the blocks leave a planner unsure
  what to do, all in wording other plans own (4e):
  * `plan-push-audit-phase` records `first..last` without the caret
    warning `PUSH-AUDIT.md:37-43` gives. That needs a v5.
  * `templates/shared-blocks/README.md:63-65` states the push-audit
    row's missing `Merged` cell without the condition
    `plan-phase-landing` attaches to it.
  * `plan-file-conventions` does not say whether a phase may be a
    prose section, which `plan-push-audit-phase` assumes.
* development#311: `MD_HEADING_CLOSING_RE`
  (`scripts/audit/text/markdown.py:105`) is quadratic on a line of
  spaces: 1.35 s at 20,000 characters, reproduced here. It is called
  from six places under `scripts/audit/checks/`, and a 2 MB line would
  exceed the leg timeout. This is outside this plan's subject (4f).
* uncalibrated-sextant#12 already exists; see *Future work*.

**Declined:**

* **4f, injected links on the compliance page**
  (`scripts/audit-update-docs.py:264`). `defuse` documents rendering
  details as prose there as a deliberate trade. A link in a committed
  page notifies nobody, and no newline survives to break the page's
  structure.
* **4e, the roster's context windows.** "The model also determines
  the context window" describes a model's capacity, which is what a
  planner choosing a model needs. A session configured below that
  capacity does not make it wrong. The figures could not be confirmed
  from anything in reach, and that is recorded here rather than
  bumping `subagent-model-roster` across eleven repositories on a
  guess.
* **4c, literal markers in fenced examples.** No `PLAN-TEMPLATE.md`
  documents the marker syntax. The mechanism is documented in
  `templates/shared-blocks/README.md`, which no check reads as a
  template.
* **4c, the smaller `PlanIndex` message defects.** These are a
  symlinked index reported as missing, decoration stripping that
  removes `_` from inside words, an escaped `\|` shifting columns, a
  table with no Status column passing "with statuses from the shared
  vocabulary", `2026-99-99` passing the date check, and `has_plans`
  being set by any link. Each is a message or an edge case, and no
  report saw any of them give a wrong verdict on a real index. They
  are recorded here so that a later change to the reader can pick
  them up.
