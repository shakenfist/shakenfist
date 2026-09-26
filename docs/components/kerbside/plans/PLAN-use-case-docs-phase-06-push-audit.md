# Use case documentation phase 6: push audit

Planning effort: medium. The judgement in a push-audit
phase is mostly spent at triage, after the reports are in,
and `PUSH-AUDIT.md` already holds the briefs. What planning
owes this phase is the range, an honest account of what is
actually in it, and the two places where the runbook's
assumptions do not match a documentation plan.

Review effort: the master plan specifies none for this
phase.

## Situation

Phases 1 to 5 of `PLAN-use-case-docs.md` have all merged.
The `plan-push-audit-phase` shared block in
`PLAN-TEMPLATE.md` makes this phase mandatory and not
optional, and requires it to run over the accumulated diff
of every phase rather than the last phase alone — because
auditing one phase at a time misses what the phases did to
each other.

This plan is a textbook case for that rule. One claim about
the backend TLS leg was stated too strongly in phase 1,
again in phase 2, again in phase 3, and only swept as a
class in phase 4; a per-phase audit would have seen each
instance as a local wording choice. Phase 5 then deleted
two sections of `docs/index.md` that phase 2 had written
`openstack.md` to supersede. Neither pattern is visible in
a single phase's diff.

## Mission

Run `PUSH-AUDIT.md` over the accumulated range, triage
every finding, fix the blocking ones, decline the rest in
writing, and close out both this phase and the master plan.

## Scope

**In scope:**

- Wave 1 and wave 2 of `PUSH-AUDIT.md` over the accumulated
  range, using `tools/audit/plan-range.sh` to derive
  `AUDIT_RANGE` and `AUDIT_PATHS`.
- The four judgment sub-agents the runbook names (style
  conformance, 2a code quality, 2b test review, 2c
  documentation review, 2d security review).
- Fixing blocking findings in this worktree, each in its
  own commit.
- Closing out phase 6 and the master plan.

**Out of scope:**

- Re-auditing `docs/use-cases/ovirt.md`'s original
  creation. Only this plan's 39 lines of change to that
  page are in range; see finding 1 for why its creation is
  not audited anywhere, and why that is permitted rather
  than a gap to close here.
- The Proxmox page. It is not a phase of this plan and has
  no source driver.
- `#468` (console rows keyed on identifier alone) and
  `#472` (`docs/proxy-architecture.md` TLS claim). Both are
  open, both were deliberately deferred by earlier phases,
  and both are code or documentation outside this plan's
  range. The audit may re-find them; the response is to
  cite the issue, not to fix it here.
- Any finding that is a defect in code this plan did not
  touch. It is filed, not fixed.

## What the survey found

Five findings. Two are false claims in the master plan.
Both were corrected at source in this phase's planning
commit, alongside phase 5's closeout, so no step below
redoes them. The other three shape the step plan.

**1. "Audited by that plan rather than this one" is
false.** `PLAN-use-case-docs.md:139-141` says the oVirt
page "landed 2026-08-10 as `PLAN-two-tier-ci-phase-04-docs.md`'s
deliverable, and is audited by that plan rather than this
one". `PLAN-two-tier-ci.md:278-283` carries a four-row
Execution table with columns `| Phase | Plan | Status |` —
no `Merged` column, no push-audit phase — and
`docs/plans/index.md:21` records the plan as `Complete`. It
predates the shared block. By the block's own rule a plan
that is already `Complete` and does not carry the phase is
not reopened to acquire one, so `ovirt.md`'s creation is
genuinely never push-audited, and that is permitted rather
than an oversight. The master plan's sentence asserts an
audit that does not exist and will not. Corrected here.

**2. The master plan carries no description of phase 6.**
Its Execution prose describes phases 1 through 5 and stops.
Both sibling plans that carry the obligation sketch the
phase — `PLAN-proxy-dev-releases.md:412` and
`PLAN-consistency-audit.md:391` each open a paragraph
`**Phase 6 — push audit.** Work through PUSH-AUDIT.md …`.
A reader of this plan alone cannot tell what its last row
means. Corrected in the same commit.

**3. The accumulated diff is not documentation-only, and
the audit is therefore not vacuous.** This is the finding
most likely to be assumed away. `plan-range.sh` over the
five merge commits derives:

```
AUDIT_RANGE=2f0e526^1..073603b
```

and a 31-path `AUDIT_PATHS`, whose diff is **5230
insertions and 130 deletions across 30 files**. Roughly 970
lines of that are Python that did not exist before this
plan:

| Path | Lines |
|------|-------|
| `tools/check-backend-tls-claims.py` | 266 |
| `tools/mutate-backend-tls-claims.py` | 138 |
| `kerbside/tests/unit/test_check_backend_tls_claims.py` | 243 |
| `kerbside/tests/unit/test_db.py` | 259 |
| `kerbside/tests/unit/test_sources_static.py` | 64 |
| `kerbside/sources/static.py` | 26 changed |

plus 91 changed lines of
`.github/workflows/functional-tests.yml` (the `docs_checks`
job and the path filter) and 14 of `demo/sources.yaml`. A
documentation plan that shipped a CI guard, a mutation
tool, a workflow job and a source-module change has real
material for every one of the runbook's judgment agents.
Do not let a step skip an agent on the grounds that "this
was a docs plan".

**4. `plan-range.sh` works on these five SHAs, unmodified.**
It was written in `PLAN-proxy-dev-releases`'s own phase 6
(step 6a) for exactly this case, and this phase does not
need to build or extend it. One cosmetic consequence to
expect rather than investigate: `AUDIT_PATHS` contains
`docs/use-cases/shaken-fist.md`, which does not exist at
the range's end, because phase 1 created it and phase 4
renamed it to `shakenfist.md`. A `git diff` restricted to a
path that no longer exists is empty, not an error, and both
paths are correctly in the set — the union is over what
each merge touched.

**5. Phase 5's definition of done holds against the tree.**
Spot-checked five of its twelve items at `073603b`: the two
deleted `docs/index.md` sections are gone; Bumblebee
appears outside `docs/plans/` in exactly
`docs/use-cases/openstack.md`; `grep -c 'use-cases/'
README.md` is 0 with a single `docs/index.md#use-cases`
link at `README.md:43`; `grep -n 'oVirt today'
ARCHITECTURE.md .claude/CLAUDE.md` returns nothing; the
`## ` heading sets of `ovirt.md` and `openstack.md` are
identical; and `tools/check-backend-tls-claims.py` exits 0
over 7 files. One item's *command* is wrong where its
*property* holds: the Bumblebee item filters with `grep -v
'^./docs/plans/'`, and this grep emits paths without the
`./` prefix, so the filter matches nothing. Not worth a
fix in a merged plan file; noted so the next reader does
not re-derive it.

## Decisions

**1. The range is `2f0e526^1..073603b`, derived rather than
written down.** Every step exports it by running
`eval "$(tools/audit/plan-range.sh 2f0e526 a7df5e5 28efa6c
8c5c042 073603b)"` rather than pasting the string. The
script validates ancestry, ordering and path safety, and a
pasted range silently skips all three. Give the SHAs
oldest-first; reversed, the derived range diffs backwards
and the style checks pass on reverted content.

**2. All five judgment agents run, including 2d security.**
The temptation is to skip 2d on a documentation plan. The
diff adds two executable Python tools, one of which
(`mutate-backend-tls-claims.py`) rewrites tracked files in
place, and changes a source module and a CI workflow. That
is precisely the shape 2d exists for. If 2d finds nothing,
that is a one-sentence result, not a reason to have
skipped it.

**3. The `docs_checks` gap is a finding for the audit to
confirm, not a fix to smuggle in.** Nothing in CI guards
the `docs/index.md#use-cases` anchor that `README.md:43`
and `ARCHITECTURE.md` now both depend on:
`kerbside/tests/unit/test_docs_links.py:82` skips every
target containing `://`, and both links are absolute. Phase
5 mitigated this with a one-time manual check and recorded
it in its risk table. It is in range and it is a real gap
that this plan opened, so 2c should find it independently.
Step 6e decides what to do with it; if 2c does not find it,
that is itself a finding about 2c's brief.

**4. Blocking findings are fixed here; advisory findings
are declined in writing, in the master plan.** The shared
block requires that a declined finding says why, in the
plan, where the next reader will find it. The place is a
short subsection under the phase 6 sketch, not a PR
comment that scrolls away.

**5. The master plan reaches `Complete` in this phase, and
phase 6 records no `Merged` cell.** A push-audit phase
closes itself out in its own pull request; it cannot know
its own merge commit. Both sibling precedents do the same —
`PLAN-proxy-dev-releases.md:270` records phase 6 as
"Complete (merged in PR #375, 2026-08-29)" in the `Status`
cell prose rather than a `Merged` SHA.

**6. The two false master-plan claims were corrected in the
planning commit, not left for the audit.** They are survey
output, not audit output, and carrying them into the
closeout would make the audit look like it found them.

## Step plan

6a runs first and alone: the runbook is explicit that wave 2
is only worth spending on if wave 1 passes. Steps 6b through
6f are independent of each other and spawn in parallel once
6a is green. 6g depends on all of them.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | low | sonnet | none | Wave 1 gates. From the repository root of this worktree, run `eval "$(tools/audit/plan-range.sh 2f0e526 a7df5e5 28efa6c 8c5c042 073603b)"` and confirm it prints `AUDIT_RANGE=2f0e526^1..073603b` and a 31-path `AUDIT_PATHS`; then run `tools/audit/wave1.sh` with both exported. Report its exit code and its output verbatim, including every advisory check, not only the fatal ones. The exit-code table is at `PUSH-AUDIT.md:55-64`: 0 pass, 1 flake8, 2 tests, 3 raw `print()` added, 4 bare `except:`, 5 cannot reach repo root, 6 `AUDIT_RANGE` does not resolve. Do not fix anything; do not re-run with a different range to make it pass. If exit is 6, stop and report — an explicitly-set range that does not resolve is fatal by design, precisely so that a mistyped range cannot audit nothing and pass. Expect the diff to contain roughly 970 lines of added Python, so a report of "no Python in the diff" means the range or paths are wrong, not that the plan was documentation-only. |
| 6b | low | sonnet | none | Wave 1 style-conformance judgment. Execute the brief under "Style conformance — judgment portion" at `PUSH-AUDIT.md:101-148`, but substitute the diff: the brief says `git diff develop...HEAD`, which is **empty** here because every phase has merged. Use `eval "$(tools/audit/plan-range.sh 2f0e526 a7df5e5 28efa6c 8c5c042 073603b)"` then `git diff "$AUDIT_RANGE" -- $AUDIT_PATHS`. The convention source the brief names as `AGENTS.md` is `.claude/CLAUDE.md` in this repository; read both. Most of the brief's checklist (SPICE parsing, source backends, API endpoints, DB access, migrations) will have no material — say so explicitly per bullet rather than silently omitting it. The bullets that do have material are logging, config and the Python style rules: 80-column wrap inside `kerbside/`, 120 elsewhere, single quotes except docstrings, never triple single quotes, no trailing whitespace, mypy type hints. `tools/check-backend-tls-claims.py` and `tools/mutate-backend-tls-claims.py` are the two new executables to read closely. Report violations with file and line, or "Style checks passed." |
| 6c | medium | sonnet | none | Wave 2 mechanical plus 2a code quality. First run `tools/audit/wave2-mechanical.sh` with the range exported as in 6a, and report its output verbatim; it never exits non-zero on findings. Then execute the 2a brief at `PUSH-AUDIT.md:183-266` with the same diff substitution as 6b, taking the mechanical output as its input. The shared blocks inside that brief are binding: `python-version-discipline` (check `requires-python` in `pyproject.toml` and hold the new Python to that floor — this is the finding to look for first, because it breaks on a real user's machine and CI runs only the newest version) and `comment-proportion`. Pay particular attention to `tools/check-backend-tls-claims.py` and `tools/mutate-backend-tls-claims.py`: they were written in phase 4 as a guard and its mutation tester, they duplicate a regex vocabulary between them by design, and the question is whether that duplication is the intended coupling or a missed abstraction. Classify each finding blocking or advisory with file and line. |
| 6d | medium | sonnet | none | 2b test review. Execute the brief at `PUSH-AUDIT.md:267-329` with the same diff substitution as 6b. The `functional-test-coverage` shared block inside it is binding. The material is `kerbside/tests/unit/test_check_backend_tls_claims.py` (243 lines), `test_db.py` (259) and `test_sources_static.py` (64), against `tools/check-backend-tls-claims.py` and the `kerbside/sources/static.py` change. Two specific questions worth answering directly. First: phase 4 committed `tools/mutate-backend-tls-claims.py`, which mutates the tracked documentation to prove the guard's rules can fail — does every rule in the guard have a mutation, and does every mutation have a test that catches it? Phase 4 reported it found four rules with no coverage at all, so the answer is checkable. Second: `test_db.py` grew 259 lines in a documentation plan — say what it covers and whether that belongs to this plan's work or arrived alongside it. Report grouped by file. |
| 6e | medium | sonnet | none | 2c documentation review. Execute the brief at `PUSH-AUDIT.md:330-467` with the same diff substitution as 6b. Its four shared blocks are all binding and all have material here: `readme-discipline` (phase 5 collapsed six README bullets to one — confirm the result is a curated link and not a feature list), `llm-doc-discipline` (`.claude/CLAUDE.md` and `ARCHITECTURE.md` both changed), `diagram-discipline` (`docs/index.md`'s mermaid broker node changed) and `plan-phase-references` (grep `README.md` and `docs/` excluding `docs/plans/` for "phase <number>"). Check independently, and report as a finding if true, that nothing in CI guards the `docs/index.md#use-cases` anchor that `README.md:43` and `ARCHITECTURE.md` now depend on — `kerbside/tests/unit/test_docs_links.py:82` skips every target containing `://` and both links are absolute. Also verify the six use-case pages still carry identical `## ` heading sets, and that no page states a backend TLS or host-subject-pinning claim more strongly than `rust/kerbside-proxy/src/backend.rs:85-108` and `:198-211` support. "No documentation gaps found" is a valid answer; a gap you were told to look for is not evidence on its own, so say whether you would have found it unprompted. |
| 6f | high | opus | none | 2d security review. Execute the brief at `PUSH-AUDIT.md:468-559` with the same diff substitution as 6b. The `path-traversal-review` shared block inside it is binding and is the one with real material: `tools/mutate-backend-tls-claims.py` rewrites tracked files in place, and `tools/check-backend-tls-claims.py` walks `DOC_PATHS` globs and opens what it finds. Both run in CI. Ask what each opens, what decides the path, and whether a path from a glob over a repository is process-chosen in the sense the block means. Also review the 91 changed lines of `.github/workflows/functional-tests.yml`: the `docs_checks` job and the `check_paths` filter, for anything that could cause a required check to pass without running, and for workflow-level injection of untrusted values into a shell. Most of the brief's classes (SPICE input validation, ticket lifecycle, SQL, TLS on the proxy legs) have no material in this diff — say so per class rather than omitting them. Report with severity, file and line. |
| 6g | high | opus | none | Triage and close out. Management session work; do not delegate the judgement. Take the reports from 6a through 6f. For each finding classify blocking or advisory, and fix the blocking ones in this worktree in their own commits with their own subjects. Then write the result into `docs/plans/PLAN-use-case-docs.md`: every finding fixed or declined **in writing with the reason**, in a short subsection under the phase 6 sketch added by 6a. If the audit found nothing, say so in one sentence — the shared block calls that a real result and a run of them is the evidence for making the phase conditional. Set phase 6 to `Complete` in the master plan's Execution table with an empty `Merged` cell, and update the `docs/plans/index.md` row. Set the master plan's own status to `Complete` if and only if nothing else in it is outstanding; Proxmox is deferred by design and does not block it, but say so rather than leaving it implied. `pre-commit run --all-files` passes. Commit subject: `docs: record the phase 6 push audit findings.` |

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| A step runs `git diff develop...HEAD` as the runbook literally says, gets an empty diff, and reports a clean audit. This is the failure mode the whole `plan-range.sh` mechanism exists to prevent, and it fails silently. | Every brief from 6b onward states the substitution explicitly and names the expected shape of the result. 6a independently asserts the derived range and the 31-path set before any judgment agent runs, and 6g rejects any report whose findings are consistent with an empty diff. |
| The SHAs are given to `plan-range.sh` in the wrong order, and the range diffs backwards — style checks then pass on reverted content. | The script rejects SHAs not given oldest-first, and 6a confirms the printed range is `2f0e526^1..073603b` before running anything. |
| An agent skips a class because "this was a documentation plan", and the skip reads as a pass. | Findings 3 and decision 2 say the diff carries ~970 lines of Python, a workflow change and a source-module change. Each brief requires an explicit per-class statement where there is no material, rather than silence. 6g treats an omitted class as an unrun check. |
| The audit re-finds `#468` or `#472` and the phase grows a code fix it should not carry. | Both are named out of scope above with their issue numbers. The response is to cite the issue. 6g is the only step permitted to fix anything, and only blocking findings inside this plan's range. |
| Triage widens into a review-comment loop, fixing advisory findings until the diff is unrecognisable. | The shared block's standard is that a declined finding says why, in the plan. 6g declines in writing rather than fixing, and the master plan is where the reason lands. |
| The master plan is marked `Complete` while `docs/plans/index.md` still says otherwise, leaving the half-finished closeout that step 1 of the next-phase skill exists to catch. | 6g changes both, and the definition of done checks both with a grep rather than a recollection. |

## Definition of done

Falsifiable items. Each is a command or a check against the
tree.

- [ ] `tools/audit/plan-range.sh 2f0e526 a7df5e5 28efa6c
      8c5c042 073603b` prints
      `export AUDIT_RANGE=2f0e526^1..073603b` and an
      `AUDIT_PATHS` of 31 paths.
- [ ] `tools/audit/wave1.sh` exits 0 with that range and
      path set exported, and its output is recorded in the
      phase's report.
- [ ] `tools/audit/wave2-mechanical.sh` has been run with
      the same environment and its output recorded.
- [ ] All five judgment agents (6b, 6c, 6d, 6e, 6f) have
      reported, and each report states a result for every
      class in its brief — including "no material" where
      that is the answer.
- [ ] Every finding appears in
      `docs/plans/PLAN-use-case-docs.md` as fixed or
      declined, and every declined finding carries a
      reason. If there were none, the plan says so in a
      sentence.
- [ ] No fix in this phase touches a file outside
      `AUDIT_PATHS`, except the plan files this phase
      writes.
- [ ] Grepping `docs/plans/PLAN-use-case-docs.md` for the
      phase 6 row shows `Complete` with an empty `Merged`
      cell.
- [ ] The master plan's phase 6 row and the
      `docs/plans/index.md` phase 6 fragment agree on the
      status, checked by reading both.
- [ ] `docs/plans/PLAN-use-case-docs.md` no longer claims
      the oVirt page is audited by `PLAN-two-tier-ci.md`,
      and carries a `**Phase 6 — push audit.**` paragraph.
- [ ] `pre-commit run --all-files` is clean.

## Registration

Registered in `docs/plans/PLAN-use-case-docs.md`'s
Execution table and in the `docs/plans/index.md` phase
fragment, in this phase's first commit, alongside phase 5's
closeout. `docs/plans/order.yml` is not touched: it
registers master plans only.

## Back brief

Read this plan back before starting, and gate on these:

1. **Before 6a**, confirm the derived range and path set
   out loud. Everything downstream is worthless if the
   range is wrong, and wrong ranges pass rather than fail.
2. **Before 6g fixes anything**, state which findings are
   blocking and why, and get agreement. The line between
   "a defect this plan introduced" and "a defect this plan
   revealed" is the whole of the triage judgement, and
   crossing it is how a closeout phase turns into a second
   implementation phase.
3. **Before marking the master plan `Complete`**, say what
   is left in it and why that does not block completion.
   Proxmox is the expected answer; a second item is a
   reason to stop and ask.
