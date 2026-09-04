# Consistency audit phase 5: diagram discipline and mermaid linting

Master plan:
[PLAN-consistency-audit.md](/components/kerbside/plans/PLAN-consistency-audit/)

**Planning effort:** medium. Almost all of the content is fixed
by the upstream template -- two files copy verbatim and a shared
block copies verbatim -- so there is little to get wrong in the
diff. The judgement is entirely about where the lane runs and
what it is allowed to block: kerbside's develop branch is behind
a merge queue with five named required checks, and the template
ships a path-filtered workflow that must not become a sixth.

## Scope

**In scope:**

- [#370](https://github.com/shakenfist/kerbside/issues/370)
  (`push-audit`), whose only live finding is the missing
  `diagram-discipline` shared block in `PUSH-AUDIT.md`.
- [#381](https://github.com/shakenfist/kerbside/issues/381)
  (`mermaid-lint-ci`): `tools/mermaid-lint.sh` and a CI workflow
  that runs it.
- The `debian-12-docker` runner label in
  `.github/actionlint.yaml`, without which actionlint fails on
  the new workflow.
- Documenting the new lane where kerbside documents its lanes:
  `docs/testing.md` and the workflow list in `.claude/CLAUDE.md`.

**Out of scope:**

- **Converting any diagram.** The `diagram-format` audit already
  passes -- *No ASCII diagrams in README.md, AGENTS.md,
  ARCHITECTURE.md or docs/* -- and every mermaid diagram in the
  repository already renders (survey finding 3). This phase adds
  the policy and the enforcement; there is nothing to remediate
  behind them.
- **Making the lane a required status check.** Decision 2.
- **The upstream `diagram-conversion` skill.** It exists in
  `shakenfist/development/.claude/skills/` and is referenced by
  the template's README, but no audit asks kerbside to carry it
  and there is nothing here to convert. Recorded under *Future
  work* in the master plan rather than adopted.
- **Teaching Renovate to bump the pinned mermaid-cli image.**
  Decision 3.

## What the survey found

The master plan's phase 5 sketch is accurate in its substance --
the two issues are one upstream change, and #370's body is
stale -- but wrong in two details, and it omits the three facts
that actually decide how the phase is built. All five findings
below were verified against `develop` at `5051f98` on
2026-09-03, and the sketch has been corrected at source as part
of the planning commit.

**1. The dates in the sketch are wrong.** It says both issues
"arrived on 2026-08-29". #370 was filed **2026-08-26** and is
not a new issue at all -- it is the pre-push audit issue phase 1
already worked on, refiled against a criterion that has since
grown a block. #381 was filed **2026-08-30**.

**2. #370's body is stale, exactly as the sketch says.** The
issue text still reads *missing shared block
path-traversal-review; missing shared block
python-version-discipline; missing shared block
functional-test-coverage* -- all three of which phase 1 added
and all three of which are present on develop
(`PUSH-AUDIT.md:218`, `:302`, `:498`). The audit files an issue
and never refreshes its body. The live checker disagrees with
the issue it filed:

```
push-audit | fail | missing shared block diagram-discipline
```

That is the whole of the finding.

**3. Every mermaid diagram in the repository already renders.**
This is the load-bearing survey result, and it was measured
rather than assumed: the upstream `mermaid-lint.sh` was run
against `develop` from a scratch copy, and all nine
diagram-bearing files passed.

```
Linting 9 file(s) containing mermaid diagrams.
ok    ARCHITECTURE.md
ok    docs/index.md
ok    docs/proxy-architecture.md
ok    docs/schema.md
ok    docs/spice/spice-link-protocol.md
ok    docs/spice/usb-redirection.md
ok    docs/spice/vd-agent-protocol.md
ok    docs/use-cases/ovirt.md
ok    tools/ovirt-e2e/README.md
```

So this phase is a pure adoption. It carries no risk of the
usual failure mode -- turning on a linter and discovering the
existing corpus does not pass it, then having to choose between
a red lane and a sweep nobody planned.

**4. actionlint really does fail without the runner label, and
the label really does exist.** Both halves were tested rather
than taken from the template's README. With the workflow staged
and `debian-12-docker` absent from `.github/actionlint.yaml`:

```
.github/workflows/mermaid-lint.yml:32:32: label "debian-12-docker"
is unknown. [runner-label]
```

With the label added, actionlint passes. The label is not
invented: `conductor/imagebuilder.py:74` in
`shakenfist/private-ci` builds an image named `debian-12-docker`
and labels its runners with it, and ryll's `ci.yml`,
`release.yml`, `supply-chain.yml` and `manual-build.yml` all
target it today. Kerbside has never used it -- every VM job here
is `debian-12` -- so this phase introduces a runner shape that is
new to this repository but not to the fleet.

Worth recording because it nearly went unnoticed: `pre-commit
run --all-files` operates on **tracked** files, so an unstaged
new workflow is silently not linted. The first attempt at this
measurement passed twice, once with the label and once without,
because actionlint never saw the file. Stage it, or measure
nothing.

**5. The two template files pass kerbside's own hooks
unmodified.** `tools/mermaid-lint.sh` copied verbatim passes
shellcheck under this repository's pin (`shellcheck-py`
v0.11.0.1, scoped to `^(tools|demo)/`), and skillsaw and the
whitespace hooks alongside it. No per-project edit is needed to
land it, which matters because decision 3 forbids editing it.

**Also corrected at source, unrelated to phase 5.** The signing
count that PR #391 fixed in the phase 4 plan survived in two
other places: `PLAN-consistency-audit.md` and the phase 4
fragment in `docs/plans/index.md` both still said **30 signed**
mark-adding commits. The corrected figure is **29**; the extra
one was `37c11de`, a merge carrying GitHub's web-flow PGP
signature rather than a gitsign x509 attestation. Re-measured on
2026-09-03 against develop:

```bash
git log --no-merges --format=%H -- REVIEWS.md '.vscode/*.weaudit*' |
    while read sha; do
        git cat-file commit "$sha" | grep -q '^gpgsig' &&
            echo signed || echo unsigned
    done | sort | uniq -c
```

gives `29 signed`, `46 unsigned`. Both stale copies are fixed in
the planning commit.

## Decisions

**Decision 1 -- take the shipped workflow, not the gate-job
fold.** The template offers two shapes: its own
path-filtered `mermaid-lint.yml`, or the script added as a step
in a job the repository's gate already covers. Kerbside cannot
take the second without a runner change. The only lint job here
is `sanity_checks` in `functional-tests.yml:112`, which runs on
`[self-hosted, static]`, and static runners have no docker
daemon -- `mmdc` renders through puppeteer and needs one. Folding
would mean moving kerbside's cheapest and most frequently run job
onto a virtual machine to lint diagrams that change on a minority
of pull requests. The shipped workflow is also the only shape
with a proven deployment: `shakenfist/development` runs
byte-identical copies of both files on itself, and a diff against
the template is how drift is meant to be found.

**Decision 2 -- the lane stays advisory and is not added to the
ruleset.** This is the decision most likely to be argued with,
because an advisory lane is one a tired person can merge past.
Three reasons it is still right here. The shipped workflow is
path-filtered to `**.md`, and a path-filtered workflow that a
branch ruleset requires never reports on a pull request that
touches no markdown, which blocks that pull request forever --
the template's README says so explicitly and it is the exact
shape of failure `tools/check-required-checks.sh` exists to
catch. Adding a sixth required check means a hand edit to the
develop ruleset, and the five current ones
(`Can see status`, `Can enqueue`, `Can merge`,
`Can enqueue: direct-qemu`, `Can enqueue: sf-e2e`) are gate jobs
precisely so that path filters stay inside the workflow rather
than in the ruleset. And advisory is what kerbside already does
with `rust.yml` and `demo-compose.yml`: path-filtered, real, and
read as a red X rather than a block. Adding `merge_group:` to the
trigger is separately refused -- `paths` is not supported on that
event, so every merge would spin a virtual machine to re-lint
diagrams the pull request already linted.

**Decision 3 -- both files copy verbatim, pinned image included,
and Renovate is not taught to bump it.** No per-project
substitution, no reflowing to kerbside's comment style, no
switching the pin to a floating tag. The template's whole
mechanism for detecting drift is that a deployed copy diffs
cleanly against `templates/mermaid-lint/`, and an "improvement"
made here becomes a permanent diff that the next person has to
adjudicate. The pin does not move on its own -- Renovate's stock
managers do not read a docker reference out of a shell script --
and that is upstream's deliberate choice, because a mermaid major
version can reject a diagram its predecessor accepted. Kerbside
inherits the choice rather than re-litigating it; if the fleet
wants automated bumps, that is one change upstream, not fifteen
downstream.

**Decision 4 -- `diagram-discipline` goes immediately before
`plan-phase-references`, not immediately after
`llm-doc-discipline`.** Upstream's own `PUSH-AUDIT.md` places it
directly after `llm-doc-discipline` ends, but kerbside has
twenty-two lines of its own documentation-review bullets between
those two blocks (`PUSH-AUDIT.md:385-405`), covering
`ARCHITECTURE.md`, `AGENTS.md` and the per-protocol pages. Those
bullets extend the prose of section 2c; interposing a shared
block would split them from what they continue. Placing the new
block just above `plan-phase-references` at line 406 keeps
kerbside's prose intact *and* preserves upstream's relative
ordering of the three documentation blocks, so a future
side-by-side against canonical still reads in the same order.

**Decision 5 -- the two issues stay in one phase.** Inherited
from the master plan's sketch and re-affirmed by the survey.
They are one upstream change: the `diagram-discipline` block is
the policy half and `templates/mermaid-lint/` is the enforcement
half, and the template's README says so in its second paragraph.
Splitting them to close #370 a day earlier would land a rule
nothing checks, then a checker for a rule already written --
each half individually reviewable but neither individually
meaningful.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | low | haiku | none | Copy the `diagram-discipline` shared block **verbatim** from `templates/shared-blocks/diagram-discipline.md` in `shakenfist/development` (read it from `origin/main`, not from a possibly stale working copy) into `PUSH-AUDIT.md` in this worktree. Insert it immediately **before** the `<!-- shared-block: plan-phase-references v1 -->` line at `PUSH-AUDIT.md:406`, separated by a blank line above and below, and not after the `llm-doc-discipline` block -- see decision 4 for why. Copy the whole block including its `<!-- shared-block: diagram-discipline v1 -->` and `<!-- shared-block-end -->` markers. Do not reflow it, do not adjust its wrapping to kerbside's 80 columns, and do not edit a word of it: the audit compares it against canonical and an edit without a version bump is invisible to every other repository. Verify with `diff <(sed -n '/shared-block: diagram-discipline/,/shared-block-end/p' PUSH-AUDIT.md) <(git -C <development> show origin/main:templates/shared-blocks/diagram-discipline.md)` -- expect no output. |
| 5b | medium | sonnet | none | Add the mermaid lint lane. Copy `templates/mermaid-lint/mermaid-lint.sh` from `shakenfist/development`'s `origin/main` to `tools/mermaid-lint.sh` (mode 0755) and `templates/mermaid-lint/mermaid-lint.yml` to `.github/workflows/mermaid-lint.yml`, both **byte for byte** -- decision 3, no substitution of any kind, and in particular leave the pinned `mermaid-cli:11.4.2` tag alone. Then add `- debian-12-docker` to the `self-hosted-runner: labels:` list in `.github/actionlint.yaml`, directly after the existing `- debian-12` entry, since the new job is the only one in the repository that needs a docker daemon; without it actionlint fails with `label "debian-12-docker" is unknown`. **`git add` all three files before running pre-commit** -- `pre-commit run --all-files` reads tracked files only, so an unstaged new workflow is silently not linted and actionlint will report a false pass (survey finding 4). Then verify three things and report all three: `pre-commit run --all-files` is clean; `./tools/mermaid-lint.sh` exits zero over all nine diagram-bearing files (check the exit status directly -- do **not** pipe it into `tail` or `grep`, which reports the filter's status and turns a failure green); and the audit's `mermaid-lint-ci` check now passes. Docker is available on this host, so the linter really can be run rather than reasoned about. |
| 5c | medium | sonnet | none | Document the new lane. In `docs/testing.md`, which is the authority on what runs where, add `mermaid-lint.yml` to the list of workflows that are in neither CI tier, saying what it does (renders every tracked markdown file's mermaid diagrams in a container and fails on a parse error), that it is path-filtered to `**.md` excluding `REVIEWS.md`, that it is deliberately **not** a required status check, and why -- a path-filtered workflow that a ruleset requires never reports on pull requests it skips, and blocks them forever (decision 2). Mention that it needs a docker-capable runner, which is why it is the only job here on `debian-12-docker`. Then add the matching one-line entry to the "Neither tier" list in `.claude/CLAUDE.md`, in the same clipped style as the entries around it -- one line, no rationale, since that file is loaded into every session and the reasoning belongs in `docs/testing.md`. Do not add anything to `AGENTS.md`: no convention changed. Keep to the repository's 80-column wrap. |

Each step is its own commit:

- 5a: `Adopt the diagram discipline shared block.`
- 5b: `Lint mermaid diagrams in CI.`
- 5c: `Document the mermaid lint lane.`

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| The `debian-12-docker` label is new to kerbside, and a job whose label matches no runner queues forever instead of failing. | The label is in production use: `conductor/imagebuilder.py:74` in `shakenfist/private-ci` builds and labels the image, and four ryll workflows target it today. If it were ever wrong, the lane is advisory and path-filtered (decision 2), so a queued job blocks no merge -- which is a second reason not to make it required in the same change that introduces the label. |
| Someone later makes the lane a required status check to stop it being ignored, and every markdown-free pull request blocks forever. | Decision 2 is written down with the mechanism, and 5c puts the same warning in `docs/testing.md` next to the lane itself, where a person editing the ruleset is likely to look. The template's README says the same thing a third time. |
| The template files are "improved" during the copy -- reflowed to 80 columns, or the image pin floated -- and kerbside acquires a permanent diff against canonical. | Decision 3, restated as a constraint in both 5a and 5b, with the verifying diff command in 5a's brief and the byte-for-byte check in the Definition of done. Survey finding 5 removes the usual excuse: the files pass kerbside's hooks unmodified. |
| A future mermaid-cli bump rejects a diagram this phase measured as passing. | Expected, and the reason the script lints every tracked markdown file rather than only the changed ones: a bump is caught by the next run over the whole corpus, not left for whoever next opens the page. The pin means the bump is deliberate. |
| The lint is measured with a pipe -- `./tools/mermaid-lint.sh \| tail` -- and reports the filter's exit status, turning every failure green. | Called out in 5b's brief and in the Definition of done. Upstream names this as how the tool was first mis-measured, so it is a documented rather than a hypothetical mistake. |
| `push-audit` still fails after 5a because the block was edited in transit. | The Definition of done compares the embedded block against canonical byte for byte, and the audit's own checker is run against the branch before the phase closes. |

## Definition of done

Every item is checkable against the branch. `<development>` is a
checkout of `shakenfist/development`. Every box below was
verified against the branch at `635cab1` on 2026-09-03, before
the pull request was opened, except the last, which cannot be
checked until it merges.

- [x] `python3 scripts/audit-check.py --repo-path <kerbside>
      --repo-name kerbside` reports `push-audit` **pass** and
      `mermaid-lint-ci` **pass**.
- [x] The `diagram-discipline` block embedded in `PUSH-AUDIT.md`
      is byte-identical to
      `templates/shared-blocks/diagram-discipline.md` at
      `origin/main`, and sits between the kerbside documentation
      bullets and `plan-phase-references`.
- [x] `tools/mermaid-lint.sh` and
      `.github/workflows/mermaid-lint.yml` are byte-identical to
      their `templates/mermaid-lint/` originals:
      `diff tools/mermaid-lint.sh <(git -C <development> show
      origin/main:templates/mermaid-lint/mermaid-lint.sh)` and
      the same for the workflow, both silent.
- [x] `tools/mermaid-lint.sh` is executable, and
      `./tools/mermaid-lint.sh` exits **zero** over nine files,
      measured from its own exit status and not through a pipe.
- [x] `.github/actionlint.yaml` lists `debian-12-docker`, and
      `pre-commit run --all-files` is clean **with the three new
      or changed files staged**.
- [x] `./tools/check-required-checks.sh` passes, and the develop
      ruleset in `.github/exported-config/` is unchanged -- the
      lane added no required check.
- [x] `docs/testing.md` lists the lane under the workflows in
      neither tier and says why it is not required; the workflow
      list in `.claude/CLAUDE.md` has one matching line; nothing
      was added to `AGENTS.md`.
- [ ] #370 and #381 close when the pull request merges, from the
      `Fixes` trailers on 5a and 5b. *This criterion originally
      said the issues should close from a passing audit run
      rather than by hand; the commits close them directly
      instead, which is the form the repository's commit
      conventions ask for.* The distinction the original wording
      was protecting -- not closing an issue while its check
      still fails -- is preserved by the first criterion above,
      which was verified before the pull request was opened, and
      by the audit itself: a check that still failed would file
      a fresh issue the next morning rather than leave the
      finding lost.

## Back brief

This phase is unusually cheap for what it closes: two issues, and
almost every line of the diff is a verbatim copy whose
correctness is a `diff` rather than a judgement. The survey is
where the value was, and it produced one result worth leading
with -- **all nine diagram-bearing files already lint clean**, so
there is no hidden remediation sitting behind the new lane.

The decision to argue with now rather than later is decision 2:
the lane is advisory. It is the weaker of the two available
guarantees, and the argument for it is not "required checks are
bad" but "this particular workflow cannot be required without
breaking every markdown-free pull request, and folding it into
the gate would move kerbside's cheapest job onto a virtual
machine". If a stronger guarantee is wanted, the honest way to
get it is a separate change that gives `sanity_checks` a
docker-capable runner and adds the script there -- a CI-shape
change with its own cost, not a line in this phase.

**Not a gate, but decide before 5b lands.** Kerbside will be the
first repository in the fleet other than `development` itself to
run this lane, so it is also the first real test of the template
outside its author's repository. If the workflow needs any edit
at all to work here, that edit belongs upstream in
`templates/mermaid-lint/` first and is copied back down -- the
same shape phase 3 took with the skillsaw checker, and for the
same reason.
