# Consistency audit phase 3: skillsaw CI detection, upstream

Master plan:
[PLAN-consistency-audit.md](/components/kerbside/plans/PLAN-consistency-audit/)

**Planning effort:** medium. The change is small and the argument for
it is strong, but it lands in another repository, in a checker that
audits nineteen repositories at once. The care is in not fixing
kerbside's finding by loosening a check everyone else relies on.

## Scope

**In scope:**

- Issue #359, by changing `check_llm_context_lint_ci()` in
  `shakenfist/development`'s `scripts/audit-check.py` so a
  workflow that demonstrably *runs* skillsaw satisfies the CI
  half, however the linter was installed.
- Tests in `scripts/test_audit_check.py` covering kerbside's
  shape, and covering the cases the widening must **not** let
  through.
- A regression sweep of every sibling repository, so the
  widening is shown to change exactly one verdict.

**Out of scope:**

- Any change to kerbside itself. This phase's only kerbside
  artefact is this plan file. `functional-tests.yml` already
  runs the linter correctly and must not be rewritten to suit
  a checker.
- `REPO_OVERRIDES`. See decision 2.
- Issues #370 and #381, the two new findings that arrived while
  phase 2 was in flight. They are local work and are now
  registered as phase 5.

## What the survey found

The master plan's phase 3 sketch is **accurate in every
particular**, which is worth stating plainly because the two
previous phases both found their sketch stale. Every claim was
re-verified against both trees today.

**Kerbside does run skillsaw in CI.**
`.github/workflows/functional-tests.yml` lines 267-272, in the
`sanity_checks` job:

```
      - name: Lint agent context with skillsaw
        run: |
          uv pip install skillsaw==0.18.0
          skillsaw --no-custom-rules .
```

**The checker cannot see it, and the reason is precise.**
`scripts/audit-check.py` line 5844 sets
`SKILLSAW_SOURCE = 'stbenjam/skillsaw'`, and
`check_llm_context_lint_ci()` (line 6020) satisfies its CI half
two ways only: a workflow that names that string outside a
comment (`named_in_ci`, line 6052), or a workflow running
`pre-commit run` while the pre-commit config names it
(`via_pre_commit`, line 6057). Kerbside installs from PyPI, so
the string appears in no workflow except a comment at line 250,
and `file_mentions()` (line 120) skips full-line comments
deliberately and for a good reason. No workflow runs
`pre-commit run`. Both routes fail; the pre-commit half passes,
because `.pre-commit-config.yaml` line 54 does name the upstream
repository.

**The specification already says the checker is wrong.**
`docs/audits/llm-context-lint-ci.md` requires that "A CI
workflow runs skillsaw", and states that "*how* skillsaw is
invoked is deliberately not pinned". It even anticipates this
class of error one paragraph earlier, about the pre-commit
route: "Requiring the linter to be named in a workflow as well
would report a repository non-compliant for a wiring that does
run it." That is exactly what is happening to kerbside, by a
different route. This phase is not asking for an exception, it
is asking the implementation to match its own written intent.

**Two facts the sketch did not know.** First,
`scripts/test_audit_check.py` exists and already carries eight
cases for this check (lines 4728-4822), so the new tests join a
suite rather than founding one; it runs as
`python3 scripts/test_audit_check.py`, wired as a local
pre-commit hook at `.pre-commit-config.yaml` line 107. Second,
`file_matches()` (line 5856) already exists with the same
comment-skipping contract as `file_mentions()`, so the widening
needs no new helper.

**Phase 1 and 2 both landed and both closed their issues by
audit run.** #368 and #373 closed at 2026-08-30T11:36-37Z, #360
at 11:41Z. Two closeout notes:

- **#370 is still open, and its body is misleading.** It lists
  the three shared blocks phase 1 added, all three of which are
  present on develop today. The audit does not refresh an open
  issue's body, so the text describes a fixed problem. #370 is
  open only because `push-audit` gained a new requirement,
  `diagram-discipline`, on 2026-08-29. Do not read it as
  evidence that phase 1 failed.
- **#360 was closed by hand**, not by the audit run, two seconds
  after PR #380 merged. The outcome is the same here because
  `ci-review-automation` was independently verified passing
  before the merge, but the plan's success criteria ask for
  closure by a passing run, and this one was not.

## Decisions

**1. Match an invocation, not a mention.** The widening adds a
third route: a workflow line that *runs* skillsaw as a command,
anchored at the start of the line. Concretely a
`SKILLSAW_RUN_RE = re.compile(r'^\s*skillsaw\b')` used through
the existing `file_matches()`. This deliberately distinguishes
kerbside's two adjacent lines: `skillsaw --no-custom-rules .`
matches and counts, while `uv pip install skillsaw==0.18.0` does
not. Installing a linter is not running it, and a check that
could not tell those apart would be worse than the one being
replaced.

**2. Fix the checker, do not exempt the repository.** A
`REPO_OVERRIDES` entry for kerbside would close #359 today and
leave the next repository that installs skillsaw from PyPI to
rediscover the same wall. The overrides table is for properties
that cannot be detected from a clone -- that a repo is docs-only,
that it is not really a Python project -- and this is not one of
those. It is a detection gap. Overrides are the fallback if the
change is rejected, not the remedy.

**3. Prove the blast radius rather than reason about it.** The
checker audits nineteen repositories, and the failure mode of a
widened check is silent: a repository that stops being reported
looks the same as one that was always fine. Step 3b therefore
runs the checker over every sibling checkout before and after
and diffs the verdicts. The expected diff is exactly one line.
This is the step most likely to be skipped as ceremony and it is
the one carrying the actual risk.

**4. The plan lives here, the change lands there.** This file is
committed to kerbside because that is where the master plan is
and where the issue was raised, but the code change is a
separate pull request against `shakenfist/development`. The two
are linked by referencing each other, not by any shared branch.
Kerbside's own tree does not change in this phase, so its
`audit-check.py` result will not move until the upstream change
merges and the next daily audit runs.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | worktree | In the **shakenfist/development** repository at `/srv/kasm_profiles/mikal/vscode/src/shakenfist/development`, create a worktree off the default branch and widen `check_llm_context_lint_ci()` in `scripts/audit-check.py` (line 6020). Today its CI half is satisfied only by `named_in_ci` (a workflow naming `SKILLSAW_SOURCE`, line 6052) or `via_pre_commit` (a workflow running `pre-commit run` with the hook configured, line 6057). Add a third route: a workflow that runs skillsaw as a command. Define `SKILLSAW_RUN_RE = re.compile(r'^\s*skillsaw\b')` beside `SKILLSAW_SOURCE` (line 5844) and test it with the existing `file_matches()` helper (line 5856), which already skips full-line comments. The anchor is load-bearing: `skillsaw --no-custom-rules .` must match and `uv pip install skillsaw==0.18.0` must not, because installing is not running. Update the function's docstring to say all three routes count. Do **not** touch `docs/audits/llm-context-lint-ci.md` -- the specification already describes the intended behaviour and this change brings the code to it, which is the argument for the change and should be stated in the commit message. |
| 3b | medium | sonnet | none | Add tests to `scripts/test_audit_check.py` for the widened check, joining the eight cases already at lines 4728-4822; follow their construction exactly. Cover four cases: (1) kerbside's shape passes -- pre-commit config naming `stbenjam/skillsaw`, plus a workflow whose run block installs skillsaw from PyPI on one line and invokes `skillsaw --no-custom-rules .` on the next; (2) a workflow that only installs it (`pip install skillsaw`) and never invokes it still **fails**; (3) a workflow mentioning skillsaw only in a full-line comment still **fails**; (4) a workflow that invokes skillsaw while the pre-commit config does **not** configure the hook still fails, on the pre-commit half. Case 2 is the one that proves decision 1 and must not be dropped. Run `python3 scripts/test_audit_check.py` and report the result. |
| 3c | medium | sonnet | none | Prove the blast radius. The sibling repository checkouts live under `/srv/kasm_profiles/mikal/vscode/src/shakenfist/` and `/srv/kasm_profiles/mikal/vscode/src/mach33labs/`. For every one that is a git checkout, run `python3 scripts/audit-check.py --repo-path <path> --repo-name <name>` twice -- once from the unmodified checker at the default branch, once from the 3a worktree -- capture the `llm-context-lint-ci` verdict for each, and diff the two sets. **The expected difference is exactly one repository: kerbside, fail to pass.** Report the full before/after table, not just the diff. If any other repository changes verdict, stop and report it without adjusting anything; that means the regex is matching something unintended and the design needs revisiting, not tuning. If the audit script is refused by a sandbox permission classifier, report that as blocked rather than as a result. |
| 3d | low | sonnet | none | Prepare the upstream pull request against `shakenfist/development` from the 3a worktree, but do **not** create it -- print the proposed title and body and stop. The body should lead with the specification quote that makes the case ("*how* skillsaw is invoked is deliberately not pinned", and the paragraph about not reporting a repository non-compliant for a wiring that does run it), state that kerbside is the repository that surfaced it, show the two adjacent lines that must be told apart, and summarise 3c's regression table. Reference kerbside issue #359 as context. Michael creates pull requests himself; offering one is the job here. |

Each step is its own commit:

- 3a: `Detect skillsaw invoked from a workflow.` (in development)
- 3b: `Test skillsaw CI detection.` (in development)
- 3c: no commit (verification)
- 3d: no commit (prepares a pull request body)

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| The widened regex quietly passes a repository that installs skillsaw but never runs it, converting a real finding into a false pass across the fleet. | The anchor `^\s*skillsaw\b` distinguishes invocation from installation, 3b case 2 asserts it directly, and 3c's before/after sweep would show any repository whose verdict moved. |
| A repository names a *file* or *directory* called skillsaw at the start of an indented line and accidentally satisfies the check. | 3c's sweep over every sibling checkout is exactly the test for this. The expected diff is one line, and any second line stops the phase rather than being explained away. |
| The change is made in kerbside instead, by rewriting `functional-tests.yml` to name `stbenjam/skillsaw` so the existing checker sees it. | Explicitly out of scope. The workflow already runs the linter correctly and the PyPI pin is deliberately kept in lockstep with the pre-commit rev; rewriting working CI to satisfy a checker's implementation detail is the compliance theatre this master plan declined once already. |
| The upstream change is rejected, and #359 stays open indefinitely. | Decision 2 names the fallback: record the divergence in the master plan and ask for a `REPO_OVERRIDES` entry with a documented reason. That is a worse outcome, not a failure of the phase. |
| Kerbside's audit result does not move when this phase is done, and the phase looks unfinished. | It cannot move: nothing in kerbside changes. #359 closes only after the upstream pull request merges and the next daily audit runs, which is a separate event on a separate timetable. The Definition of done is written against the upstream repository for this reason. |

## Definition of done

- [ ] `check_llm_context_lint_ci()` in `shakenfist/development`
      recognises a workflow that invokes skillsaw, however it was
      installed, and its docstring says so.
- [ ] `python3 scripts/test_audit_check.py` passes, including a
      new case asserting that installing skillsaw without
      invoking it still fails.
- [ ] Running the modified checker across every sibling
      checkout changes exactly one verdict:
      `llm-context-lint-ci` for kerbside, fail to pass. The
      before/after table is in the phase's report, not merely
      asserted.
- [ ] `pre-commit run --all-files` is clean in the development
      worktree.
- [ ] **No file in the kerbside repository has changed** except
      this plan, the master plan and `docs/plans/index.md`.
      `git diff --stat develop` proves it.
- [ ] A pull request body against `shakenfist/development` has
      been drafted and shown to Michael, who creates it.
- [ ] Issue #359 closes from a passing audit run after that pull
      request merges, not by hand.

## Back brief

This is the first phase whose work lands outside kerbside, so
the reporting matters more than usual: the kerbside tree will
look untouched and the audit numbers will not move, and both of
those are correct rather than evidence of nothing happening.

Two gates:

- **Gate 1, after 3c and before 3d.** The regression table is
  the whole safety argument for changing a checker that judges
  nineteen repositories. Show it in full. A one-line diff is the
  pass condition; anything else stops the phase.
- **Gate 2, at 3d.** The pull request is not created by the
  session. The drafted title and body go to Michael, who decides
  whether and when to open it against `shakenfist/development`.

One inherited item to carry, not to fix here: #370 remains open
with a body describing an already-solved problem, because the
audit does not refresh issue bodies. Phase 5 owns it. Anyone
reading #370 before then should check `PUSH-AUDIT.md` rather
than trust the issue text.
