# Consistency audit phase 1: shared blocks, vendor stamp and settings closeout

Master plan:
[PLAN-consistency-audit.md](/components/kerbside/plans/PLAN-consistency-audit/)

**Planning effort:** medium. Every change in this phase has a
canonical source to copy from, so the difficulty is care rather
than judgment: getting block boundaries exact, removing the
hand-written prose the blocks replace, and re-vendoring without
leaving a shared checkout mutated.

## Scope

**In scope:**

- Issue #368 -- add the `plan-push-audit-phase` v2 shared block
  to `PLAN-TEMPLATE.md`, and remove the hand-written paragraph
  it supersedes.
- Issue #370 -- add the `path-traversal-review`,
  `python-version-discipline` and `functional-test-coverage`
  shared blocks to `PUSH-AUDIT.md`.
- Issue #373 -- re-vendor sfui so
  `kerbside/api/static/sfui/.sfui-commit` names canonical HEAD.
- The residue of the old standalone plan: confirm the
  `github-security` audit covers the three settings ticked by
  hand in July, so nothing is lost by the master plan having
  dropped the checkbox section.

**Out of scope:**

- #360, the comment addresser (phase 2). Deliberately split;
  see decision 3 in the master plan.
- #359, skillsaw CI detection (phase 3). The fix is in another
  repository. Do not touch
  `.github/workflows/functional-tests.yml` in this phase --
  see decision 2 in the master plan for why making the string
  match succeed is refused.
- #227, review coverage (phase 4).
- Adding a `vendor.sh --check` pre-commit hook or CI step. The
  survey found kerbside has neither, and the daily audit is
  the only thing that notices drift. That is a real gap, but
  it is a new capability rather than a fix for #373, and it is
  recorded in the master plan's Future work instead.

## What the survey found

Surveyed 2026-08-29 against `develop` at `fe4bebe`. The
master plan carries the full survey; this section records
only what an implementer of this phase needs, with the
citations to check it.

**The audit checker runs locally, so this phase has a
mechanical done-criterion.** `scripts/audit-check.py` in
`shakenfist/development` takes `--repo-path` and prints JSON.
Against `develop` at `fe4bebe` it reports 41 checks: 31 pass,
6 fail, 4 not applicable. The six failures are exactly the six
open issues:

```
llm-context-lint-ci   skillsaw does not run from a CI workflow
ci-review-automation  the retired comment addresser is still deployed
push-audit            missing shared block path-traversal-review ...
plan-template         missing shared block plan-push-audit-phase
review-coverage       124 of 194 in-scope files reviewed; 70 need review
sfui-vendor           2 commit(s) behind canonical
```

After this phase, `push-audit`, `plan-template` and
`sfui-vendor` must pass and the failure count must be 3.

**`PLAN-TEMPLATE.md` carries a hand-written predecessor of the
block, which must go.** Lines 123-134 are a paragraph beginning
"The last row of every master plan's phase table is a push
audit:", sitting under the `### Phase status` heading (line 91)
after the `plan-status-vocabulary` block. It says roughly what
the shared block says, in different words and without the
`Merged` column. Leaving it would state the same policy twice
and disagree about the column. `ryll` hit this exact situation
and removed the paragraph; see below.

**`ryll` sets the placement precedent.** In
`../ryll-wt-push-audit/PLAN-TEMPLATE.md` the block sits at line
68, inside `## Execution` and before the
`plan-status-vocabulary` block at line 144 -- not under a
`Phase status` sub-heading. `grep 'last row of every master
plan'` there returns nothing: the prose was deleted. ryll also
added a short project-specific note *after* the block saying
where the `Merged` column goes in that repository's tables and
that each phase lands as one merge commit. Follow both moves.

**`AGENTS.md` already references `PUSH-AUDIT.md`.** Line 29,
in the question table. The `push-audit` audit checks for that
reference as well as the blocks, and it is already satisfied,
so this phase adds blocks only.

**The sfui re-vendor changes the stamp and nothing else.**
`.sfui-commit` holds `190383aecb319eddb0f586a567c460fe545bb86b`.
sfui's default branch is `develop` (not `main`) and its HEAD is
`c3f65ae0aa0d793e62537e139bc06bf605be1218`. `git diff` between
those two commits touches one file,
`.github/workflows/renovate.yml`, a Renovate bump of
`renovatebot/github-action`. That file is not in `vendor.sh`'s
distributable list (`README.md`, `tokens.css`, `sf.css`,
`sf-theme.js`, `shakenfist-logo.svg`, `lit-core.min.js`,
`morphdom-umd.js`, plus `components/`). Expect a one-line diff.
If any asset changes, stop: something else has drifted and the
phase's assumption is wrong.

**The local sfui checkout is stale and shared.**
`/srv/kasm_profiles/mikal/vscode/src/shakenfist/sfui` sits at
`190383a` with no local `origin/develop` ref. It is a working
checkout, not scratch space. Do not check it out to a new
commit and leave it there; see decision 2 below.

## Decisions

**Decision 1 -- the blocks go where the audit's own sections
put them, and `ryll` decides the ambiguous one.** The
`push-audit` specification says `path-traversal-review`,
`python-version-discipline` and `functional-test-coverage`
carry "the three criteria delegated to the reviewer because no
grep can judge them", but does not name sections.
`path-traversal-review` belongs in `### 2d. Security review`
(line 372), which is unambiguous. The other two have no
obvious home: `python-version-discipline` and
`functional-test-coverage` are placed in `### 2b. Test review`
(line 227) and `### 2a. Code quality` (line 166) respectively,
because that is where a reviewer already has the relevant
material open. Read each block before placing it -- if a block
plainly describes something else, place it where it reads
correctly and say so in the commit message. The audit checks
that the blocks are present and verbatim, not where they sit,
so a defensible placement cannot fail the check.

*Corrected during implementation:* the guess above was
backwards and step 1b caught it. `functional-test-coverage`
is about whether tests exercise the real thing rather than a
mock, which is section 2b's subject; `python-version-discipline`
is about the syntax and typing floor in `requires-python`,
which is section 2a's. They were swapped, and the step table
above now names the sections they actually went into. The
instruction to read each block before placing it is what
found this, and is worth keeping in future briefs.

**Decision 2 -- re-vendor from a throwaway clone, not from the
working checkout.** `tools/vendor.sh` must run from a checkout
at the commit being vendored, and the shared sfui checkout is
at the old commit with uncommitted-state risk. Clone into the
scratchpad, check out `c3f65ae`, run `vendor.sh` from there,
and delete the clone. This costs seconds and cannot leave
somebody else's working tree on a detached HEAD. It also
sidesteps `tools/verify-vendor-deps.sh`, which gates the copy
and fails on a half-synced checkout.

**Decision 3 -- verify the blocks with `diff`, not by eye.**
Every block has a canonical file. The done-criteria below
include a command that extracts each embedded block from the
kerbside file and diffs it against
`shakenfist/development/templates/shared-blocks/`. A block
that is one character off passes a human read and fails the
audit, which is the whole reason the audit exists.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | Add the `plan-push-audit-phase` shared block to `PLAN-TEMPLATE.md` in the kerbside repository, and delete the hand-written paragraph it replaces. Copy the block **verbatim**, including its `<!-- shared-block: plan-push-audit-phase v2 -->` opening marker and `<!-- shared-block-end -->` closing marker, from `/srv/kasm_profiles/mikal/vscode/src/shakenfist/development/templates/shared-blocks/plan-push-audit-phase.md` (66 lines). Place it inside the `## Execution` section (line 87), before the `### Phase status` sub-heading and its `plan-status-vocabulary` block, matching `/srv/kasm_profiles/mikal/vscode/src/shakenfist/ryll-wt-push-audit/PLAN-TEMPLATE.md` where the same block sits at line 68. Then **delete lines 123-134**, the paragraph starting "The last row of every master plan's phase table is a push audit:" and ending "worth having." -- it is a hand-written predecessor of the block and would otherwise state the policy twice, in conflicting terms, since it never mentions the `Merged` column. Finally add a short project-specific note after the block, in the style of ryll's (see lines immediately after its block), saying that in kerbside the Execution phases are a table and `Merged` is its last column, after `Status`. Do not reflow or reword the block; `diff` against the canonical file must be empty. |
| 1b | medium | sonnet | none | Add three shared blocks to `PUSH-AUDIT.md` in the kerbside repository, each copied **verbatim** including both markers, from `/srv/kasm_profiles/mikal/vscode/src/shakenfist/development/templates/shared-blocks/`: `path-traversal-review.md` (26 lines) into `### 2d. Security review` (line 372); `functional-test-coverage.md` (25 lines) into `### 2b. Test review` (line 227); `python-version-discipline.md` (22 lines) into `### 2a. Code quality` (line 166). Read each block first -- if one plainly belongs in a different section than the one named here, put it where it reads correctly and note the deviation in the commit message; the audit checks presence and byte-equality, not section. Follow the placement style of the blocks already in the file: `comment-proportion` at line 201, `readme-discipline` at 277, `llm-doc-discipline` at 295, `plan-phase-references` at 340 -- each sits as its own paragraph within its section, with a blank line either side. Do not modify `AGENTS.md`; its reference to `PUSH-AUDIT.md` at line 29 already satisfies the other half of this audit. |
| 1c | low | haiku | none | Re-vendor the sfui design system into kerbside. Work from a throwaway clone, not from `/srv/kasm_profiles/mikal/vscode/src/shakenfist/sfui`, which is a shared working checkout that must be left untouched. In the scratchpad directory: `git clone https://github.com/shakenfist/sfui`, `git checkout c3f65ae0aa0d793e62537e139bc06bf605be1218`, then from that clone run `tools/vendor.sh <kerbside-worktree>/kerbside/api/static/sfui`. Then run `tools/vendor.sh --check <same path>` from the same clone and confirm it exits zero. Delete the clone. The expected result in kerbside is a **one-line diff**: `.sfui-commit` changing from `190383aecb319eddb0f586a567c460fe545bb86b` to `c3f65ae0aa0d793e62537e139bc06bf605be1218`. If `git status` shows any other file changed, stop and report -- the two upstream commits touch only `.github/workflows/renovate.yml`, which is not distributed, so an asset change means something has drifted and needs a human. |
| 1d | low | haiku | none | Confirm the `github-security` consistency audit covers the three GitHub settings the old standalone plan ticked by hand in July: Dependabot security updates, secret scanning, and secret scanning push protection. Read `/srv/kasm_profiles/mikal/vscode/src/shakenfist/development/docs/audits/github-security.md` and the corresponding `check_github_security` function in that repository's `scripts/audit-check.py`. Report which of the three the audit actually checks and which, if any, it does not. Change no files -- this is a research step whose output goes into the master plan's Future work bullet, which currently asserts the audit covers them without having checked. If the audit does not cover all three, say so and the bullet gets rewritten rather than removed. |
| 1e | medium | sonnet | none | Verify the phase. Run, from the kerbside worktree: (1) `pre-commit run --all-files`; (2) `tox -e py3` and `tox -e flake8`; (3) the block-equality check in the Definition of done below, which must print nothing; (4) `python3 /srv/kasm_profiles/mikal/vscode/src/shakenfist/development/scripts/audit-check.py --repo-path <worktree> --repo-name kerbside`, and confirm the failure count has dropped from 6 to 3 with `push-audit`, `plan-template` and `sfui-vendor` now passing. Report each result verbatim, including failures. Do not fix anything found here without reporting it first -- a check that newly fails is information about steps 1a-1c, not a nuisance to be silenced. |

Each step is its own commit:

- 1a: `Adopt the push audit phase shared block.`
- 1b: `Adopt three delegated review shared blocks.`
- 1c: `Re-vendor sfui at canonical HEAD.`
- 1d: no commit (research; folds into the 1a-1c series or a
  small master-plan edit).
- 1e: no commit (verification).

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| A block is copied with a reflowed line, a smart quote, or a stale version marker, and the audit still fails after the phase lands. | Step 1e's `diff` loop compares each embedded block against its canonical file and must print nothing. The management session runs it too, per the master plan's review checklist -- this is the "review by `diff`, not by reading" case. |
| Deleting `PLAN-TEMPLATE.md` lines 123-134 removes wording some existing plan depends on. | The paragraph is guidance for authors, not a reference target. Before committing 1a, `grep -rn 'last row of every master plan' docs/` to confirm nothing quotes it. The block that replaces it says more, not less. |
| The sfui re-vendor pulls in an asset change nobody reviewed, on a phase whose whole premise is that it will not. | 1c asserts a one-line diff and stops otherwise. The premise was verified by `git diff 190383a c3f65ae --stat` during the survey; if it no longer holds, sfui has moved again and the phase re-surveys rather than proceeding. |
| Re-vendoring from the shared sfui checkout leaves it on a detached HEAD, breaking somebody's in-flight work. | Decision 2: clone to the scratchpad, vendor from there, delete it. 1c's brief says this explicitly and names the path not to touch. |
| The vendored copy has no drift check in pre-commit or CI, so it goes stale again within days. | Out of scope here and recorded in the master plan's Future work. `vendor.sh --check` exists and is documented as usable as a consumer CI step, so the fix is cheap when it is scheduled. |
| The phase lands, but the GitHub issues stay open because nothing re-runs the audit. | The issues close automatically on the next daily audit run. Do not close them by hand -- the master plan's success criteria require closure by a passing run, so a hand-closed issue hides whether the fix actually worked. |

## Definition of done

- [ ] `PLAN-TEMPLATE.md` contains a `plan-push-audit-phase` block
      at version v2, and no longer contains the string `last row
      of every master plan`.
- [ ] `PUSH-AUDIT.md` contains `path-traversal-review`,
      `python-version-discipline` and `functional-test-coverage`
      blocks.
- [ ] Every embedded block is byte-identical to its canonical
      copy. This command prints nothing:

```bash
DEV=/srv/kasm_profiles/mikal/vscode/src/shakenfist/development
for f in PLAN-TEMPLATE.md PUSH-AUDIT.md; do
  grep -o 'shared-block: [a-z-]*' "$f" | cut -d' ' -f2 | while read -r b; do
    awk "/<!-- shared-block: $b v/,/<!-- shared-block-end -->/" "$f" \
      | diff - "$DEV/templates/shared-blocks/$b.md" || echo "MISMATCH: $b in $f"
  done
done
```

- [ ] `kerbside/api/static/sfui/.sfui-commit` reads
      `c3f65ae0aa0d793e62537e139bc06bf605be1218`, and
      `git diff --stat develop -- kerbside/api/static/sfui/`
      shows exactly one file changed.
- [ ] `tools/vendor.sh --check kerbside/api/static/sfui`, run
      from an sfui clone at that commit, exits zero.
- [ ] `audit-check.py --repo-path <worktree> --repo-name
      kerbside` reports 3 failures, not 6, and none of them is
      `push-audit`, `plan-template` or `sfui-vendor`.
- [ ] `pre-commit run --all-files` is clean.
- [ ] `tox -e py3` and `tox -e flake8` pass.
- [ ] The master plan's Future work bullet about the three July
      security settings states what step 1d actually found,
      rather than an assumption.
- [ ] Issues #368, #370 and #373 are closed by a passing
      consistency audit run, not by hand.

## Back brief

Before executing any step, back brief the operator on your
understanding of this phase and how the work you intend to do
aligns with it.

Two gates, both cheap to propose and expensive to redo:

1. **Before step 1a edits anything**, show the operator the
   exact lines you intend to delete from `PLAN-TEMPLATE.md`
   and where you intend to insert the block. Deleting the
   wrong paragraph is invisible in review and changes what the
   template tells every future plan author.
2. **After step 1c, before committing**, show the operator
   `git status --short` and the `.sfui-commit` diff. The
   phase's premise is that this is a one-line change; if it is
   not, the operator decides whether to proceed rather than the
   implementing agent.
