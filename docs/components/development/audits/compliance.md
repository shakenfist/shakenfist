# Consistency audit compliance

Which project currently meets which criterion. This page is
regenerated weekly by the consistency audit workflow and
committed by it, and it is the only generated file in `docs/audits/`
-- every criterion specification beside it is hand-written and
changes only when a person changes it.

Read a table here together with the criterion it belongs to. The
table says who passes and which issue tracks each failure -- `-` in
the Issue column means none is open, and `?` that the lookup failed,
so whether one is open is not known; the
specification says what is checked and why, what the check
deliberately does not cover, and which template implements it, and is
what to read first when picking up an issue. [README.md](/components/development/audits/README/)
indexes them all.

The generation timestamp below is load-bearing. When a run fails it
leaves the previous run's verdicts in place, so this page goes on
looking healthy while being stale -- check the date before trusting a
verdict. The audit runs at 18:00 UTC every Sunday and can be
dispatched by hand in between, so a timestamp older than the most
recent Sunday 18:00 UTC means the last scheduled run failed, however
many manual runs came before it. See
[../consistency-audits.md](/components/development/consistency-audits/) for what a run
does.

<!-- consistency-audit:begin -->
*Generated 2026-11-01T21:49:08.169695+00:00 from `scripts/audit-check.py`; do not edit.*

## ci-review-automation

Criterion: [ci-review-automation.md](/components/development/audits/ci-review-automation/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | non-compliant | shakenfist/client-python#408 |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#121 |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | non-compliant | shakenfist/divergulent#120 |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | non-compliant | shakenfist/kerbside-client#5 |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#16 |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **client-python** (Status): pr-re-review.yml does not confirm that the tree it checked out is the commit it resolved, so a push between the two is reviewed with no warning; pr-retest.yml: job trigger-retest does not export pr-bot-trigger's same-repo output, so the job that needs it cannot require a same-repository pull request
- **clingwrap** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; pr-re-review.yml does not confirm that the tree it checked out is the commit it resolved, so a push between the two is reviewed with no warning; pr-retest.yml: job trigger-retest does not export pr-bot-trigger's same-repo output, so the job that needs it cannot require a same-repository pull request; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py, tools/review-schema.json); it is unused, and its workflow holds contents: write on the pull request branch
- **divergulent** (Status): pr-re-review.yml does not confirm that the tree it checked out is the commit it resolved, so a push between the two is reviewed with no warning; pr-retest.yml: job trigger-retest does not export pr-bot-trigger's same-repo output, so the job that needs it cannot require a same-repository pull request
- **kerbside-client** (Status): Missing pr-re-review.yml; Missing pr-retest.yml; No automated reviewer: no workflow other than pr-re-review.yml or pr-retest.yml calls shakenfist/actions/.github/workflows/pr-auto-review.yml@main or uses shared action review-pr-with-claude@main
- **uncalibrated-sextant** (Status): Missing pr-re-review.yml; Missing pr-retest.yml; No automated reviewer: no workflow other than pr-re-review.yml or pr-retest.yml calls shakenfist/actions/.github/workflows/pr-auto-review.yml@main or uses shared action review-pr-with-claude@main

## console-logging

Criterion: [console-logging.md](/components/development/audits/console-logging/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | compliant | - |
| andris | N/A | - |
| client-python | compliant | - |
| client-python-k3s | N/A | - |
| client-python-ova | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | N/A | - |
| images | N/A | - |
| instar | N/A | - |
| kerbside | N/A | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

## default-branch-naming

Criterion: [default-branch-naming.md](/components/development/audits/default-branch-naming/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | compliant | - |

## delete-branch-on-merge

Criterion: [delete-branch-on-merge.md](/components/development/audits/delete-branch-on-merge/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | compliant | - |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | non-compliant | shakenfist/kerbside-client#9 |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#21 |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **kerbside-client** (Status): Delete branch on merge is not enabled
- **uncalibrated-sextant** (Status): Delete branch on merge is not enabled

## dependency-name-normalization

Criterion: [dependency-name-normalization.md](/components/development/audits/dependency-name-normalization/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | compliant | - |
| andris | N/A | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | compliant | - |
| hunkydory | N/A | - |
| images | N/A | - |
| instar | N/A | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

## diagram-format

Criterion: [diagram-format.md](/components/development/audits/diagram-format/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#126 |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **clingwrap** (Status): 1 diagram(s) drawn in ASCII rather than mermaid (convert them, or mark a block that is genuinely better drawn by hand with an "audit-ok: diagram-format" comment above the fence): ARCHITECTURE.md:21

## docs-external-links

Criterion: [docs-external-links.md](/components/development/audits/docs-external-links/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | compliant | - |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#7 |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **uncalibrated-sextant** (Status): 67 relative link(s) in docs/ that do not resolve to a file inside docs/ (use absolute https://github.com/... URLs, which survive the docs site import): docs/plans/PLAN-audit-cleanup-phase-02-structural.md -> ../../src/bootloader.rs, docs/plans/PLAN-audit-cleanup-phase-02-structural.md -> ../../src/renderer/mod.rs, docs/plans/PLAN-audit-cleanup-phase-02-structural.md -> ../../src/scene.rs, docs/plans/PLAN-audit-cleanup-phase-03-tests.md -> ../../Makefile, docs/plans/PLAN-audit-cleanup-phase-03-tests.md -> ../../scripts/screenshot.sh, docs/plans/PLAN-audit-cleanup-phase-03-tests.md -> ../../scripts/verify-release.sh, docs/plans/PLAN-audit-cleanup-phase-03-tests.md -> ../../src/scene.rs, docs/plans/PLAN-audit-cleanup.md -> ../../AGENTS.md, docs/plans/PLAN-audit-cleanup.md -> ../../ARCHITECTURE.md, docs/plans/PLAN-audit-cleanup.md -> ../../PUSH-AUDIT.md (+57 more)

## docs-line-references

Criterion: [docs-line-references.md](/components/development/audits/docs-line-references/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | compliant | - |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | compliant | - |

## eol-distro

Criterion: [eol-distro.md](/components/development/audits/eol-distro/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | compliant | - |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | compliant | - |

## eol-producers

Criterion: [eol-producers.md](/components/development/audits/eol-producers/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| andris | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| client-python-ova | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | N/A | - |
| images | N/A | - |
| instar | N/A | - |
| kerbside | N/A | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | compliant | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | N/A | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

## expensive-lane-path-filter

Criterion: [expensive-lane-path-filter.md](/components/development/audits/expensive-lane-path-filter/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#118 |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **clingwrap** (Status): 1 expensive lane(s) triggered by pull_request or merge_group without adequate path filtering: functional-tests.yml (no path filtering). Add a check_paths filter job (see kerbside functional-tests.yml) or, only for workflows backing no required status check, trigger-level paths-ignore, excluding docs/** and the review-tracking files; mark deliberate exceptions with an "audit-ok: no-path-filter" comment

## export-repo-config

Criterion: [export-repo-config.md](/components/development/audits/export-repo-config/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | compliant | - |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | non-compliant | shakenfist/kerbside-client#7 |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#19 |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **kerbside-client** (Status): Missing .github/workflows/export-repo-config.yml
- **uncalibrated-sextant** (Status): Missing .github/workflows/export-repo-config.yml

## fuzz-nightly-reporting

Criterion: [fuzz-nightly-reporting.md](/components/development/audits/fuzz-nightly-reporting/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| andris | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| client-python-ova | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | N/A | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | N/A | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | N/A | - |
| shakenfist | N/A | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

## github-security

Criterion: [github-security.md](/components/development/audits/github-security/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | compliant | - |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | non-compliant | shakenfist/kerbside-client#8 |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#20 |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **kerbside-client** (Status): Missing .github/workflows/codeql-analysis.yml; Secret scanning not enabled; Secret scanning push protection not enabled
- **uncalibrated-sextant** (Status): Missing .github/workflows/codeql-analysis.yml; Secret scanning not enabled; Secret scanning push protection not enabled

## llm-context-lint-ci

Criterion: [llm-context-lint-ci.md](/components/development/audits/llm-context-lint-ci/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#120 |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#6 |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **clingwrap** (Status): skillsaw does not run from .pre-commit-config.yaml or a CI workflow
- **uncalibrated-sextant** (Status): skillsaw does not run from .pre-commit-config.yaml or a CI workflow

## llm-context-lint

Criterion: [llm-context-lint.md](/components/development/audits/llm-context-lint/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | compliant | - |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | compliant | - |

## llm-doc-naming

Criterion: [llm-doc-naming.md](/components/development/audits/llm-doc-naming/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | non-compliant | shakenfist/client-python#406 |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | compliant | - |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **client-python** (Status): 1 agent instruction file is named for a single tool rather than AGENTS.md (CLAUDE.md); AGENTS.md is tracked beside it, so it is a second set of instructions loaded with equal authority: merge what is still true into AGENTS.md and delete the original

## llm-doc-structure

Criterion: [llm-doc-structure.md](/components/development/audits/llm-doc-structure/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | compliant | - |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | non-compliant | shakenfist/kerbside#648 |
| kerbside-client | N/A | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **kerbside** (Status): ARCHITECTURE.md is 508 lines / 3487 words (limits: 500 lines, 4000 words); move detail into docs/ and leave a summary and a link

## llm-tooling

Criterion: [llm-tooling.md](/components/development/audits/llm-tooling/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | compliant | - |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | non-compliant | shakenfist/kerbside-client#1 |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **kerbside-client** (Status): Missing: AGENTS.md, ARCHITECTURE.md

## merge-group-cancellation

Criterion: [merge-group-cancellation.md](/components/development/audits/merge-group-cancellation/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

## merge-queue-config

Criterion: [merge-queue-config.md](/components/development/audits/merge-queue-config/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

## mermaid-lint-ci

Criterion: [mermaid-lint-ci.md](/components/development/audits/mermaid-lint-ci/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| andris | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| client-python-ova | N/A | - |
| clingwrap | N/A | - |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | N/A | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

## npm-pin-indirect-dependencies

Criterion: [npm-pin-indirect-dependencies.md](/components/development/audits/npm-pin-indirect-dependencies/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| andris | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| client-python-ova | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | N/A | - |
| kerbside | N/A | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | N/A | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

## npm-undeclared-direct-dependency

Criterion: [npm-undeclared-direct-dependency.md](/components/development/audits/npm-undeclared-direct-dependency/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| andris | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| client-python-ova | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | N/A | - |
| kerbside | N/A | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | N/A | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

## npm-unused-declared-dependency

Criterion: [npm-unused-declared-dependency.md](/components/development/audits/npm-unused-declared-dependency/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| andris | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| client-python-ova | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | N/A | - |
| kerbside | N/A | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | N/A | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

## pin-indirect-dependencies

Criterion: [pin-indirect-dependencies.md](/components/development/audits/pin-indirect-dependencies/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| andris | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| client-python-ova | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | N/A | - |
| images | N/A | - |
| instar | N/A | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

## plan-audit-phase

Criterion: [plan-audit-phase.md](/components/development/audits/plan-audit-phase/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | N/A | - |
| andris | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | N/A | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#10 |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **uncalibrated-sextant** (Status): 5 of 5 incomplete master plan(s) do not end with a phase running PUSH-AUDIT.md, which the plan-push-audit-phase shared block requires; each is named with the fix it needs: PLAN-locked-bootloader.md (no push audit phase; phase 3 is "3. Iteration, documentation, inventory closeout"), PLAN-display-mode-keystrokes.md (no push audit phase; phase 3 is "3. Iteration against ryll display-mode-ui, documentation,..."), PLAN-audit-cleanup.md (no push audit phase; phase 3 is "3. Test coverage and release verification"), PLAN-visual-digest.md (no push audit phase; phase 3 is "3. Repaint integration, format spec, closeout"), PLAN-continuous-digest.md (no push audit phase; phase 3 is "3. Docs, decoder coordination, closeout"); 2 plan(s) with no phases this check can read, not judged: PLAN-language-probes.md, PLAN-headless-readback-bug.md

## plan-history-in-source

Criterion: [plan-history-in-source.md](/components/development/audits/plan-history-in-source/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | non-compliant | shakenfist/client-python#425 |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#139 |
| client-python-ova | compliant | - |
| clingwrap | compliant | - |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | non-compliant | shakenfist/divergulent#129 |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | non-compliant | shakenfist/shakenfist#4728 |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#22 |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **client-python** (Status): 19 plan history reference(s) in source, in 3 file(s) (write the reason the code is this way instead of citing the plan step that produced it)
- **client-python-k3s** (Status): 63 plan history reference(s) in source, in 14 file(s) (write the reason the code is this way instead of citing the plan step that produced it)
- **divergulent** (Status): 206 plan history reference(s) in source, in 43 file(s) (write the reason the code is this way instead of citing the plan step that produced it)
- **shakenfist** (Status): 1 plan history reference(s) in source, in 1 file(s) (write the reason the code is this way instead of citing the plan step that produced it)
- **uncalibrated-sextant** (Status): 22 plan history reference(s) in source, in 5 file(s) (write the reason the code is this way instead of citing the plan step that produced it)

## plan-index

Criterion: [plan-index.md](/components/development/audits/plan-index/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | N/A | - |
| andris | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | N/A | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | compliant | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#9 |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **uncalibrated-sextant** (Status): 7 status cell(s) outside the shared vocabulary (Proposed, Not started, In progress, Blocked, Complete, Abandoned, Superseded): Locked bootloader ("Complete (commits a7b261d through thi..."), Display-mode keystrokes ("Complete (commits 455d2b5 through thi..."), Audit cleanup ("Complete (commits 1482eb0 through thi..."), Visual on-screen digest ("Complete (commits 55844a5 through thi..."), Continuous multi-channel visual digest ("Complete (commits 8814fab through thi...") (+2 more)

## plan-phase-references

Criterion: [plan-phase-references.md](/components/development/audits/plan-phase-references/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | non-compliant | shakenfist/client-python#382 |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | compliant | - |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | non-compliant | shakenfist/private-ci#259 |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#8 |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **client-python** (Status): 1 plan phase reference(s) in documentation (describe the current behaviour, or link the master plan in docs/plans/ instead of citing a phase number): AGENTS.md:142
- **private-ci** (Status): 18 plan phase reference(s) in documentation (describe the current behaviour, or link the master plan in docs/plans/ instead of citing a phase number): docs/capacity-claims.md:251, docs/capacity-claims.md:448, docs/capacity-claims.md:506, docs/capacity-claims.md:561, docs/capacity-claims.md:564, docs/capacity-claims.md:603, docs/capacity-claims.md:738, docs/capacity-claims.md:741, docs/capacity-claims.md:767, docs/capacity-claims.md:1032 (+8 more)
- **uncalibrated-sextant** (Status): 25 plan phase reference(s) in documentation (describe the current behaviour, or link the master plan in docs/plans/ instead of citing a phase number): AGENTS.md:104, AGENTS.md:126, AGENTS.md:145, AGENTS.md:166, ARCHITECTURE.md:3, ARCHITECTURE.md:10, ARCHITECTURE.md:16, ARCHITECTURE.md:29, ARCHITECTURE.md:44, ARCHITECTURE.md:137 (+15 more)

## plan-source-references

Criterion: [plan-source-references.md](/components/development/audits/plan-source-references/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| andris | N/A | - |
| client-python | non-compliant | shakenfist/client-python#403 |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | compliant | - |
| hunkydory | N/A | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | compliant | - |
| ryll | compliant | - |
| sfui | N/A | - |
| shakenfist | non-compliant | shakenfist/shakenfist#4727 |
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **client-python** (Status): 2 of 4 plan reference(s) in source or configuration do not resolve, in 2 file(s) (update the path, or use an absolute https://github.com/... URL for a plan in another repository)
- **shakenfist** (Status): 1 of 313 plan reference(s) in source or configuration do not resolve, in 1 file(s) (update the path, or use an absolute https://github.com/... URL for a plan in another repository)

## plan-template

Criterion: [plan-template.md](/components/development/audits/plan-template/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| andris | compliant | - |
| client-python | N/A | - |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#157 |
| client-python-ova | compliant | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | non-compliant | shakenfist/divergulent#127 |
| hunkydory | N/A | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | compliant | - |
| library-utilities | N/A | - |
| occystrap | compliant | - |
| private-ci | compliant | - |
| ryll | compliant | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#12 |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **client-python-k3s** (Status): shared block plan-push-audit-phase is stale (v3 embedded, v4 current; copy it verbatim from templates/shared-blocks/plan-push-audit-phase.md in the development repository)
- **divergulent** (Status): shared block plan-file-conventions is stale (v1 embedded, v2 current; copy it verbatim from templates/shared-blocks/plan-file-conventions.md in the development repository); shared block plan-push-audit-phase is stale (v3 embedded, v4 current; copy it verbatim from templates/shared-blocks/plan-push-audit-phase.md in the development repository)
- **uncalibrated-sextant** (Status): shared block plan-file-conventions is stale (v1 embedded, v2 current; copy it verbatim from templates/shared-blocks/plan-file-conventions.md in the development repository); missing shared block plan-status-vocabulary (copy it verbatim from templates/shared-blocks/plan-status-vocabulary.md in the development repository); missing shared block plan-push-audit-phase (copy it verbatim from templates/shared-blocks/plan-push-audit-phase.md in the development repository); missing shared block plan-phase-landing (copy it verbatim from templates/shared-blocks/plan-phase-landing.md in the development repository)

## pre-commit-node-version

Criterion: [pre-commit-node-version.md](/components/development/audits/pre-commit-node-version/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| andris | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| client-python-ova | N/A | - |
| clingwrap | N/A | - |
| cloudgood | compliant | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | N/A | - |
| images | N/A | - |
| instar | N/A | - |
| kerbside | N/A | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | N/A | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

## push-audit

Criterion: [push-audit.md](/components/development/audits/push-audit/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | N/A | - |
| andris | compliant | - |
| client-python | N/A | - |
| client-python-k3s | compliant | - |
| client-python-ova | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#11 |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **uncalibrated-sextant** (Status): missing shared block llm-doc-discipline (copy it verbatim from templates/shared-blocks/llm-doc-discipline.md in the development repository); missing shared block diagram-discipline (copy it verbatim from templates/shared-blocks/diagram-discipline.md in the development repository); missing shared block comment-proportion (copy it verbatim from templates/shared-blocks/comment-proportion.md in the development repository); missing shared block source-file-size (copy it verbatim from templates/shared-blocks/source-file-size.md in the development repository); missing shared block plan-references-in-code (copy it verbatim from templates/shared-blocks/plan-references-in-code.md in the development repository); missing shared block plan-phase-references (copy it verbatim from templates/shared-blocks/plan-phase-references.md in the development repository); missing shared block path-traversal-review (copy it verbatim from templates/shared-blocks/path-traversal-review.md in the development repository); missing shared block python-version-discipline (copy it verbatim from templates/shared-blocks/python-version-discipline.md in the development repository); missing shared block functional-test-coverage (copy it verbatim from templates/shared-blocks/functional-test-coverage.md in the development repository); AGENTS.md does not reference PUSH-AUDIT.md (an audit nothing points at does not get run)

## pyproject-usage

Criterion: [pyproject-usage.md](/components/development/audits/pyproject-usage/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | compliant | - |
| andris | N/A | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | compliant | - |
| hunkydory | N/A | - |
| images | N/A | - |
| instar | N/A | - |
| kerbside | compliant | - |
| kerbside-client | non-compliant | shakenfist/kerbside-client#3 |
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **kerbside-client** (Status): 6 Python file(s) but no pyproject.toml

## python-version

Criterion: [python-version.md](/components/development/audits/python-version/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | compliant | - |
| andris | N/A | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | compliant | - |
| hunkydory | N/A | - |
| images | N/A | - |
| instar | N/A | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

## readme-absolute-links

Criterion: [readme-absolute-links.md](/components/development/audits/readme-absolute-links/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#108 |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **clingwrap** (Status): 5 relative link target(s) in README.md (use absolute URLs so the README renders off the repo landing page): AGENTS.md, ARCHITECTURE.md, RELEASE-SETUP.md, docs/, docs/index.md

## readme-structure

Criterion: [readme-structure.md](/components/development/audits/readme-structure/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | compliant | - |

## release-artifacts-exclude-plans

Criterion: [release-artifacts-exclude-plans.md](/components/development/audits/release-artifacts-exclude-plans/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| andris | N/A | - |
| client-python | non-compliant | shakenfist/client-python#426 |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | non-compliant | shakenfist/divergulent#130 |
| hunkydory | N/A | - |
| images | N/A | - |
| instar | N/A | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **client-python** (Status): 1 release artifact(s) would ship docs/plans/, which is working history rather than source; exclude it with the mechanism each finding names
- **divergulent** (Status): 1 release artifact(s) would ship docs/plans/, which is working history rather than source; exclude it with the mechanism each finding names

## release-environment-protection

Criterion: [release-environment-protection.md](/components/development/audits/release-environment-protection/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

## release-merge-queue

Criterion: [release-merge-queue.md](/components/development/audits/release-merge-queue/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | non-compliant | shakenfist/occystrap#182 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **occystrap** (Status): No merge queue on default branch "develop", but release.yml releases from it: add a merge_queue rule to the branch's ruleset

## release-process

Criterion: [release-process.md](/components/development/audits/release-process/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

## renovate-lockstep-groups

Criterion: [renovate-lockstep-groups.md](/components/development/audits/renovate-lockstep-groups/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| andris | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| client-python-ova | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | N/A | - |
| images | N/A | - |
| instar | N/A | - |
| kerbside | N/A | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

## renovate

Criterion: [renovate.md](/components/development/audits/renovate/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | non-compliant | shakenfist/actions#226 |
| agent-python | non-compliant | shakenfist/agent-python#159 |
| andris | non-compliant | shakenfist/andris#68 |
| client-python | non-compliant | shakenfist/client-python#437 |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#158 |
| client-python-ova | non-compliant | shakenfist/client-python-ova#50 |
| clingwrap | non-compliant | shakenfist/clingwrap#117 |
| cloudgood | non-compliant | shakenfist/cloudgood#26 |
| development | compliant | - |
| divergulent | non-compliant | shakenfist/divergulent#136 |
| hunkydory | non-compliant | shakenfist/hunkydory#141 |
| images | N/A | - |
| instar | non-compliant | shakenfist/instar#868 |
| kerbside | non-compliant | shakenfist/kerbside#676 |
| kerbside-client | non-compliant | shakenfist/kerbside-client#2 |
| kerbside-patches | non-compliant | shakenfist/kerbside-patches#2046 |
| library-utilities | non-compliant | shakenfist/library-utilities#90 |
| occystrap | non-compliant | shakenfist/occystrap#185 |
| private-ci | N/A | - |
| ryll | non-compliant | shakenfist/ryll#680 |
| sfui | non-compliant | shakenfist/sfui#65 |
| shakenfist | non-compliant | shakenfist/shakenfist#4763 |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#13 |
| visual-digest-rust | non-compliant | shakenfist/visual-digest-rust#67 |

Details for non-compliant projects:

- **actions** (Status): renovate.yml differs from templates/renovate/renovate.yml beyond the versions Renovate bumps (line 59 is `- name: Self-hosted Renovate` where the template has `# Installed in a step of its own, with no secret in its`; the template's `run: |` is missing before line 63; line 69 is `run: npx --yes "renovate@${PINNED_RENOVATE_VERSION}"` where the template has `run: "${RUNNER_TEMP}/renovate-bin/node_modules/.bin/renovate"`); copy the template verbatim
- **agent-python** (Status): renovate.yml runs Renovate through renovatebot/github-action, which needs a Docker daemon the runner may not have; copy the template, which runs it from npm; renovate.json does not extend customManagers:githubActionsVersions, so the Renovate version pinned in renovate.yml is never bumped
- **andris** (Status): renovate.yml differs from templates/renovate/renovate.yml beyond the versions Renovate bumps (line 59 is `- name: Self-hosted Renovate` where the template has `# Installed in a step of its own, with no secret in its`; the template's `run: |` is missing before line 63; line 69 is `run: npx --yes "renovate@${PINNED_RENOVATE_VERSION}"` where the template has `run: "${RUNNER_TEMP}/renovate-bin/node_modules/.bin/renovate"`); copy the template verbatim
- **client-python** (Status): renovate.yml runs Renovate through renovatebot/github-action, which needs a Docker daemon the runner may not have; copy the template, which runs it from npm; renovate.json does not extend customManagers:githubActionsVersions, so the Renovate version pinned in renovate.yml is never bumped
- **client-python-k3s** (Status): renovate.yml runs Renovate through renovatebot/github-action, which needs a Docker daemon the runner may not have; copy the template, which runs it from npm; renovate.json does not extend customManagers:githubActionsVersions, so the Renovate version pinned in renovate.yml is never bumped
- **client-python-ova** (Status): renovate.yml differs from templates/renovate/renovate.yml beyond the versions Renovate bumps (line 59 is `- name: Self-hosted Renovate` where the template has `# Installed in a step of its own, with no secret in its`; the template's `run: |` is missing before line 63; line 69 is `run: npx --yes "renovate@${PINNED_RENOVATE_VERSION}"` where the template has `run: "${RUNNER_TEMP}/renovate-bin/node_modules/.bin/renovate"`); copy the template verbatim
- **clingwrap** (Status): renovate.yml runs Renovate through renovatebot/github-action, which needs a Docker daemon the runner may not have; copy the template, which runs it from npm; renovate.json does not extend customManagers:githubActionsVersions, so the Renovate version pinned in renovate.yml is never bumped; renovate.json does not enable the pre-commit manager, so the hook revisions in .pre-commit-config.yaml are unmanaged and drift silently
- **cloudgood** (Status): renovate.yml differs from templates/renovate/renovate.yml beyond the versions Renovate bumps (line 59 is `- name: Self-hosted Renovate` where the template has `# Installed in a step of its own, with no secret in its`; the template's `run: |` is missing before line 63; line 69 is `run: npx --yes "renovate@${PINNED_RENOVATE_VERSION}"` where the template has `run: "${RUNNER_TEMP}/renovate-bin/node_modules/.bin/renovate"`); copy the template verbatim
- **divergulent** (Status): renovate.yml runs Renovate through renovatebot/github-action, which needs a Docker daemon the runner may not have; copy the template, which runs it from npm; renovate.json does not extend customManagers:githubActionsVersions, so the Renovate version pinned in renovate.yml is never bumped
- **hunkydory** (Status): renovate.yml differs from templates/renovate/renovate.yml beyond the versions Renovate bumps (line 59 is `- name: Self-hosted Renovate` where the template has `# Installed in a step of its own, with no secret in its`; the template's `run: |` is missing before line 63; line 69 is `run: npx --yes "renovate@${PINNED_RENOVATE_VERSION}"` where the template has `run: "${RUNNER_TEMP}/renovate-bin/node_modules/.bin/renovate"`); copy the template verbatim
- **instar** (Status): renovate.yml differs from templates/renovate/renovate.yml beyond the versions Renovate bumps (line 59 is `- name: Self-hosted Renovate` where the template has `# Installed in a step of its own, with no secret in its`; the template's `run: |` is missing before line 63; line 69 is `run: npx --yes "renovate@${PINNED_RENOVATE_VERSION}"` where the template has `run: "${RUNNER_TEMP}/renovate-bin/node_modules/.bin/renovate"`); copy the template verbatim
- **kerbside** (Status): renovate.yml differs from templates/renovate/renovate.yml beyond the versions Renovate bumps (line 59 is `- name: Self-hosted Renovate` where the template has `# Installed in a step of its own, with no secret in its`; the template's `run: |` is missing before line 63; line 69 is `run: npx --yes "renovate@${PINNED_RENOVATE_VERSION}"` where the template has `run: "${RUNNER_TEMP}/renovate-bin/node_modules/.bin/renovate"`); copy the template verbatim
- **kerbside-client** (Status): Missing: .github/workflows/renovate.yml, renovate.json
- **kerbside-patches** (Status): renovate.yml differs from templates/renovate/renovate.yml beyond the versions Renovate bumps (line 59 is `- name: Self-hosted Renovate` where the template has `# Installed in a step of its own, with no secret in its`; the template's `run: |` is missing before line 63; line 69 is `run: npx --yes "renovate@${PINNED_RENOVATE_VERSION}"` where the template has `run: "${RUNNER_TEMP}/renovate-bin/node_modules/.bin/renovate"`); copy the template verbatim
- **library-utilities** (Status): renovate.yml differs from templates/renovate/renovate.yml beyond the versions Renovate bumps (line 59 is `- name: Self-hosted Renovate` where the template has `# Installed in a step of its own, with no secret in its`; the template's `run: |` is missing before line 63; line 69 is `run: npx --yes "renovate@${PINNED_RENOVATE_VERSION}"` where the template has `run: "${RUNNER_TEMP}/renovate-bin/node_modules/.bin/renovate"`); copy the template verbatim
- **occystrap** (Status): renovate.yml differs from templates/renovate/renovate.yml beyond the versions Renovate bumps (line 59 is `- name: Self-hosted Renovate` where the template has `# Installed in a step of its own, with no secret in its`; the template's `run: |` is missing before line 63; line 69 is `run: npx --yes "renovate@${PINNED_RENOVATE_VERSION}"` where the template has `run: "${RUNNER_TEMP}/renovate-bin/node_modules/.bin/renovate"`); copy the template verbatim
- **ryll** (Status): renovate.yml differs from templates/renovate/renovate.yml beyond the versions Renovate bumps (line 59 is `- name: Self-hosted Renovate` where the template has `# Installed in a step of its own, with no secret in its`; the template's `run: |` is missing before line 63; line 69 is `run: npx --yes "renovate@${PINNED_RENOVATE_VERSION}"` where the template has `run: "${RUNNER_TEMP}/renovate-bin/node_modules/.bin/renovate"`); copy the template verbatim
- **sfui** (Status): renovate.yml differs from templates/renovate/renovate.yml beyond the versions Renovate bumps (line 59 is `- name: Self-hosted Renovate` where the template has `# Installed in a step of its own, with no secret in its`; the template's `run: |` is missing before line 63; line 69 is `run: npx --yes "renovate@${PINNED_RENOVATE_VERSION}"` where the template has `run: "${RUNNER_TEMP}/renovate-bin/node_modules/.bin/renovate"`); copy the template verbatim
- **shakenfist** (Status): renovate.yml differs from templates/renovate/renovate.yml beyond the versions Renovate bumps (line 59 is `- name: Self-hosted Renovate` where the template has `# Installed in a step of its own, with no secret in its`; the template's `run: |` is missing before line 63; line 69 is `run: npx --yes "renovate@${PINNED_RENOVATE_VERSION}"` where the template has `run: "${RUNNER_TEMP}/renovate-bin/node_modules/.bin/renovate"`); copy the template verbatim
- **uncalibrated-sextant** (Status): Missing: .github/workflows/renovate.yml, renovate.json
- **visual-digest-rust** (Status): renovate.yml differs from templates/renovate/renovate.yml beyond the versions Renovate bumps (line 59 is `- name: Self-hosted Renovate` where the template has `# Installed in a step of its own, with no secret in its`; the template's `run: |` is missing before line 63; line 69 is `run: npx --yes "renovate@${PINNED_RENOVATE_VERSION}"` where the template has `run: "${RUNNER_TEMP}/renovate-bin/node_modules/.bin/renovate"`); copy the template verbatim

## reusable-workflow-secrets

Criterion: [reusable-workflow-secrets.md](/components/development/audits/reusable-workflow-secrets/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | compliant | - |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | compliant | - |

## review-coverage

Criterion: [review-coverage.md](/components/development/audits/review-coverage/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | non-compliant | shakenfist/actions#227 |
| agent-python | N/A | - |
| andris | non-compliant | shakenfist/andris#58 |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| client-python-ova | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | non-compliant | shakenfist/development#247 |
| divergulent | N/A | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | N/A | - |
| kerbside | non-compliant | shakenfist/kerbside#227 |
| kerbside-client | non-compliant | shakenfist/kerbside-client#12 |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | non-compliant | shakenfist/ryll#456 |
| sfui | non-compliant | shakenfist/sfui#42 |
| shakenfist | N/A | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **actions** (Status): 152 of 167 in-scope files reviewed at HEAD (2 imported from shakenfist/development); 15 need review (threshold 5)
- **andris** (Status): 9 of 134 in-scope files reviewed at HEAD (9 imported from shakenfist/development); 125 need review (threshold 5)
- **development** (Status): 229 of 235 in-scope files reviewed at HEAD; 6 need review (threshold 5)
- **kerbside** (Status): 91 of 356 in-scope files reviewed at HEAD (7 imported from shakenfist/development); 265 need review (threshold 5)
- **kerbside-client** (Status): 3 of 15 in-scope files reviewed at HEAD (3 imported from shakenfist/development); 12 need review (threshold 5)
- **ryll** (Status): 185 of 289 in-scope files reviewed at HEAD (6 imported from shakenfist/development); 104 need review (threshold 5)
- **sfui** (Status): 6 of 49 in-scope files reviewed at HEAD (6 imported from shakenfist/development); 43 need review (threshold 5)

## review-scope-completeness

Criterion: [review-scope-completeness.md](/components/development/audits/review-scope-completeness/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | N/A | - |
| andris | compliant | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| client-python-ova | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | N/A | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | N/A | - |
| kerbside | non-compliant | shakenfist/kerbside#650 |
| kerbside-client | compliant | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | non-compliant | shakenfist/ryll#681 |
| sfui | compliant | - |
| shakenfist | N/A | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **kerbside** (Status): 4 tracked file(s) are out of review scope only because no include pattern in .vscode/review-scope.toml names them
- **ryll** (Status): 13 tracked file(s) are out of review scope only because no include pattern in .vscode/review-scope.toml names them

## review-tracking-templates

Criterion: [review-tracking-templates.md](/components/development/audits/review-tracking-templates/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | non-compliant | shakenfist/actions#228 |
| agent-python | N/A | - |
| andris | compliant | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| client-python-ova | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | N/A | - |
| hunkydory | non-compliant | shakenfist/hunkydory#142 |
| images | N/A | - |
| instar | N/A | - |
| kerbside | non-compliant | shakenfist/kerbside#651 |
| kerbside-client | non-compliant | shakenfist/kerbside-client#14 |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | non-compliant | shakenfist/sfui#61 |
| shakenfist | N/A | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **actions** (Status): .github/workflows/prune-reviews.yml differs from templates/review-tracking/prune-reviews.yml; tools/ci-prune-reviews.sh differs from templates/review-tracking/ci-prune-reviews.sh. Copy them byte for byte from templates/review-tracking/ in shakenfist/development
- **hunkydory** (Status): .github/workflows/prune-reviews.yml differs from templates/review-tracking/prune-reviews.yml; tools/ci-prune-reviews.sh differs from templates/review-tracking/ci-prune-reviews.sh. Copy them byte for byte from templates/review-tracking/ in shakenfist/development
- **kerbside** (Status): .github/workflows/prune-reviews.yml differs from templates/review-tracking/prune-reviews.yml; tools/ci-prune-reviews.sh differs from templates/review-tracking/ci-prune-reviews.sh. Copy them byte for byte from templates/review-tracking/ in shakenfist/development
- **kerbside-client** (Status): .github/workflows/prune-reviews.yml differs from templates/review-tracking/prune-reviews.yml; tools/ci-prune-reviews.sh differs from templates/review-tracking/ci-prune-reviews.sh. Copy them byte for byte from templates/review-tracking/ in shakenfist/development
- **sfui** (Status): .github/workflows/prune-reviews.yml differs from templates/review-tracking/prune-reviews.yml; tools/ci-prune-reviews.sh differs from templates/review-tracking/ci-prune-reviews.sh. Copy them byte for byte from templates/review-tracking/ in shakenfist/development

## rust-unwrap-lint

Criterion: [rust-unwrap-lint.md](/components/development/audits/rust-unwrap-lint/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| andris | compliant | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| client-python-ova | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | N/A | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | N/A | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | N/A | - |
| shakenfist | N/A | - |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#14 |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **uncalibrated-sextant** (Status): clippy unwrap_used lint not set to warn or deny in Cargo.toml; clippy.toml missing allow-unwrap-in-tests = true

## scheduled-workflow-health

Criterion: [scheduled-workflow-health.md](/components/development/audits/scheduled-workflow-health/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | compliant | - |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | compliant | - |

## scope-coverage

Criterion: [scope-coverage.md](/components/development/audits/scope-coverage/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| andris | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| client-python-ova | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | N/A | - |
| hunkydory | N/A | - |
| images | N/A | - |
| instar | N/A | - |
| kerbside | N/A | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | N/A | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

## secret-handling

Criterion: [secret-handling.md](/components/development/audits/secret-handling/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| andris | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#111 |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | non-compliant | shakenfist/kerbside-client#11 |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#18 |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **clingwrap** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
- **kerbside-client** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
- **uncalibrated-sextant** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow

## security-sanitization

Criterion: [security-sanitization.md](/components/development/audits/security-sanitization/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| andris | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| client-python-ova | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | N/A | - |
| images | N/A | - |
| instar | N/A | - |
| kerbside | N/A | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

## sfui-vendor

Criterion: [sfui-vendor.md](/components/development/audits/sfui-vendor/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| andris | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| client-python-ova | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | N/A | - |
| images | N/A | - |
| instar | N/A | - |
| kerbside | non-compliant | shakenfist/kerbside#606 |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | non-compliant | shakenfist/private-ci#260 |
| ryll | non-compliant | shakenfist/ryll#682 |
| sfui | N/A | - |
| shakenfist | N/A | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **kerbside** (Status): kerbside/api/static/sfui: 27 commit(s) behind canonical; re-run tools/vendor.sh from an up to date sfui checkout
- **private-ci** (Status): conductor/static/sfui: 32 commit(s) behind canonical; re-run tools/vendor.sh from an up to date sfui checkout
- **ryll** (Status): ryll/src/web/assets/sfui: 3 commit(s) behind canonical; re-run tools/vendor.sh from an up to date sfui checkout

## undeclared-direct-dependency

Criterion: [undeclared-direct-dependency.md](/components/development/audits/undeclared-direct-dependency/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| andris | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| client-python-ova | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | N/A | - |
| images | N/A | - |
| instar | N/A | - |
| kerbside | non-compliant | shakenfist/kerbside#649 |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **kerbside** (Status): Imported but declared only as a transitive pin: Jinja2 (pyproject.toml:122). Declare each above the # START_OF_INDIRECT_DEPS marker; the reconciler drops the generated copy on its next run

## unused-declared-dependency

Criterion: [unused-declared-dependency.md](/components/development/audits/unused-declared-dependency/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | compliant | - |
| andris | N/A | - |
| client-python | non-compliant | shakenfist/client-python#383 |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | compliant | - |
| hunkydory | N/A | - |
| images | N/A | - |
| instar | N/A | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **client-python** (Status): Declared but never imported: chardet (pyproject.toml:23), pyyaml (pyproject.toml:27), requests_toolbelt (pyproject.toml:22). Remove each, or record why it is installed with a "# not-imported: <name> -- <reason>" comment in the dependencies array

## version-file-gitignore

Criterion: [version-file-gitignore.md](/components/development/audits/version-file-gitignore/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | compliant | - |
| andris | N/A | - |
| client-python | compliant | - |
| client-python-k3s | N/A | - |
| client-python-ova | N/A | - |
| clingwrap | non-compliant | shakenfist/clingwrap#106 |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | compliant | - |
| hunkydory | N/A | - |
| images | N/A | - |
| instar | N/A | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **clingwrap** (Status): clingwrap/_version.py is not covered by .gitignore

## workflow-standards

Criterion: [workflow-standards.md](/components/development/audits/workflow-standards/)

| Project | flake8wrap | Runners | Static tags | VM size | Permissions | Linting | devpi fallback | devpi IP | Review scope | Review marks | Issue |
|---------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|
| actions | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | compliant | compliant | - |
| agent-python | compliant | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | N/A | - |
| andris | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | compliant | compliant | - |
| client-python | compliant | compliant | compliant | non-compliant | compliant | compliant | N/A | compliant | N/A | N/A | shakenfist/client-python#378 |
| client-python-k3s | compliant | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | N/A | - |
| client-python-ova | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | N/A | - |
| clingwrap | compliant | compliant | compliant | non-compliant | compliant | compliant | N/A | compliant | N/A | N/A | shakenfist/clingwrap#125 |
| cloudgood | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | N/A | - |
| development | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | compliant | N/A | - |
| divergulent | compliant | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | N/A | - |
| hunkydory | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | compliant | N/A | - |
| images | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | - |
| instar | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | N/A | - |
| kerbside | compliant | compliant | compliant | compliant | compliant | compliant | compliant | compliant | compliant | compliant | - |
| kerbside-client | non-compliant | compliant | compliant | compliant | compliant | non-compliant | N/A | compliant | N/A | N/A | shakenfist/kerbside-client#4, shakenfist/kerbside-client#6 |
| kerbside-patches | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | N/A | - |
| library-utilities | compliant | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | N/A | - |
| occystrap | compliant | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | N/A | - |
| private-ci | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | - |
| ryll | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | compliant | N/A | - |
| sfui | N/A | compliant | compliant | compliant | compliant | compliant | compliant | compliant | compliant | compliant | - |
| shakenfist | compliant | compliant | compliant | compliant | compliant | compliant | compliant | compliant | N/A | N/A | - |
| uncalibrated-sextant | N/A | non-compliant | compliant | compliant | non-compliant | compliant | N/A | compliant | N/A | N/A | shakenfist/uncalibrated-sextant#15, shakenfist/uncalibrated-sextant#17 |
| visual-digest-rust | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | N/A | - |

Details for non-compliant projects:

- **client-python** (VM size): 3 "vm" runner job(s) naming no size: code-formatting.yml:19 (self-hosted, vm), functional-tests.yml:102 (self-hosted, vm), supply-chain.yml:81 (self-hosted, vm). The conductor takes the runner size from the labels and falls back to the first CI_SIZES entry -- "xs", one vCPU and 2048 MB -- when it finds none, so an omitted size is a silent downgrade to the smallest runner rather than a free choice. Add the size the job actually wants (xs/s/m/l/xl; m-bigdisk/xl-bigdisk when the job needs the disk; s-cloud/s-cluster when it builds a nested cloud or cluster); "xs" is a valid answer stated explicitly. A job which genuinely cannot name one marks the line "audit-ok: vm-runner-size" with the reason
- **clingwrap** (VM size): 1 "vm" runner job(s) naming no size: functional-tests.yml:31 (self-hosted, vm, debian-13). The conductor takes the runner size from the labels and falls back to the first CI_SIZES entry -- "xs", one vCPU and 2048 MB -- when it finds none, so an omitted size is a silent downgrade to the smallest runner rather than a free choice. Add the size the job actually wants (xs/s/m/l/xl; m-bigdisk/xl-bigdisk when the job needs the disk; s-cloud/s-cluster when it builds a nested cloud or cluster); "xs" is a valid answer stated explicitly. A job which genuinely cannot name one marks the line "audit-ok: vm-runner-size" with the reason
- **kerbside-client** (flake8wrap): Missing shellcheck disable=SC2086 directive
- **kerbside-client** (Linting): Missing .pre-commit-config.yaml
- **uncalibrated-sextant** (Runners): 1 unmarked GitHub-hosted runner reference(s): pre-commit.yml:10 (ubuntu-latest). Move to a self-hosted runner, or mark deliberate exceptions with an "audit-ok: github-hosted-runner" comment
- **uncalibrated-sextant** (Permissions): 1 workflow(s) missing top-level permissions: pre-commit.yml

## Criteria with no automated check

These criteria are written down and judged by a person, so they have no table above. Each says why in its own page:

- [test-coverage.md](/components/development/audits/test-coverage/)
<!-- consistency-audit:end -->
