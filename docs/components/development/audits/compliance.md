# Consistency audit compliance

Which project currently meets which criterion. This page is
regenerated every morning by the consistency audit workflow and
committed by it, and it is the only generated file in `docs/audits/`
-- every criterion specification beside it is hand-written and
changes only when a person changes it.

Read a table here together with the criterion it belongs to. The
table says who passes and which issue tracks each failure; the
specification says what is checked and why, what the check
deliberately does not cover, and which template implements it, and is
what to read first when picking up an issue. [README.md](/components/development/audits/README/)
indexes them all.

The generation timestamp below is load-bearing. When a run fails it
leaves the previous run's verdicts in place, so this page goes on
looking healthy while being stale -- check the date before trusting a
verdict, and see
[../consistency-audits.md](/components/development/consistency-audits/) for what a run
does.

<!-- consistency-audit:begin -->
*Generated 2026-09-24T11:22:42.032909+00:00 from `scripts/audit-check.py`; do not edit.*

## ci-review-automation

Criterion: [ci-review-automation.md](/components/development/audits/ci-review-automation/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#121 |
| cloudgood | non-compliant | shakenfist/cloudgood#1 |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | non-compliant | shakenfist/kerbside-client#5 |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | non-compliant | shakenfist/sfui#26 |
| shakenfist | compliant | - |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#16 |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **clingwrap** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py, tools/review-schema.json); it is unused, and its workflow holds contents: write on the pull request branch
- **cloudgood** (Status): Missing workflows: pr-re-review.yml
- **kerbside-client** (Status): Missing pr-re-review.yml; Missing pr-retest.yml; No workflow uses shared action review-pr-with-claude@main
- **sfui** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py); it is unused, and its workflow holds contents: write on the pull request branch
- **uncalibrated-sextant** (Status): Missing pr-re-review.yml; Missing pr-retest.yml; No workflow uses shared action review-pr-with-claude@main

## console-logging

Criterion: [console-logging.md](/components/development/audits/console-logging/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | compliant | - |
| client-python | compliant | - |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | N/A | - |
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
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
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
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | compliant | - |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
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
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | compliant | - |
| hunkydory | N/A | - |
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
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#126 |
| cloudgood | non-compliant | shakenfist/cloudgood#9 |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
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
- **cloudgood** (Status): 1 diagram(s) drawn in ASCII rather than mermaid (convert them, or mark a block that is genuinely better drawn by hand with an "audit-ok: diagram-format" comment above the fence): docs/memory-mapped-devices.md:729

## docs-external-links

Criterion: [docs-external-links.md](/components/development/audits/docs-external-links/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | compliant | - |
| cloudgood | non-compliant | shakenfist/cloudgood#7 |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| instar | non-compliant | shakenfist/instar#585 |
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

- **cloudgood** (Status): 2 relative link(s) in docs/ that do not resolve to a file inside docs/ (use absolute https://github.com/... URLs, which survive the docs site import): docs/index.md -> more-fundamentals.md, docs/virtualization-history.md -> more-fundamentals.md
- **instar** (Status): 1 relative link(s) in docs/ that do not resolve to a file inside docs/ (use absolute https://github.com/... URLs, which survive the docs site import): docs/plans/PLAN-differencing-phase-07-guest-host.md -> docs/plans/PLAN-differencing.md
- **uncalibrated-sextant** (Status): 67 relative link(s) in docs/ that do not resolve to a file inside docs/ (use absolute https://github.com/... URLs, which survive the docs site import): docs/plans/PLAN-audit-cleanup-phase-02-structural.md -> ../../src/bootloader.rs, docs/plans/PLAN-audit-cleanup-phase-02-structural.md -> ../../src/renderer/mod.rs, docs/plans/PLAN-audit-cleanup-phase-02-structural.md -> ../../src/scene.rs, docs/plans/PLAN-audit-cleanup-phase-03-tests.md -> ../../Makefile, docs/plans/PLAN-audit-cleanup-phase-03-tests.md -> ../../scripts/screenshot.sh, docs/plans/PLAN-audit-cleanup-phase-03-tests.md -> ../../scripts/verify-release.sh, docs/plans/PLAN-audit-cleanup-phase-03-tests.md -> ../../src/scene.rs, docs/plans/PLAN-audit-cleanup.md -> ../../AGENTS.md, docs/plans/PLAN-audit-cleanup.md -> ../../ARCHITECTURE.md, docs/plans/PLAN-audit-cleanup.md -> ../../PUSH-AUDIT.md (+57 more)

## eol-distro

Criterion: [eol-distro.md](/components/development/audits/eol-distro/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | non-compliant | shakenfist/occystrap#138 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | non-compliant | shakenfist/shakenfist#4204 |
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **occystrap** (Status): 5 reference(s) to end-of-life distribution releases. Debian 12 (bookworm) reached end of life on 2026-06-10; use the debian-13 runner labels, or a debian:13 (trixie) image. Moving a runner label also means declaring the new one in .github/actionlint.yaml in the same commit, or the workflow fails actionlint. A reference that must stay -- test input built on the old release, say -- is marked "audit-ok: eol-distro" with the reason, on the line or the line above
- **shakenfist** (Status): 5 reference(s) to end-of-life distribution releases. Debian 12 (bookworm) reached end of life on 2026-06-10; use the debian-13 runner labels, or a debian:13 (trixie) image. Moving a runner label also means declaring the new one in .github/actionlint.yaml in the same commit, or the workflow fails actionlint. A reference that must stay -- test input built on the old release, say -- is marked "audit-ok: eol-distro" with the reason, on the line or the line above

## expensive-lane-path-filter

Criterion: [expensive-lane-path-filter.md](/components/development/audits/expensive-lane-path-filter/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#118 |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | non-compliant | shakenfist/sfui#14 |
| shakenfist | compliant | - |
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **clingwrap** (Status): 1 expensive lane(s) triggered by pull_request or merge_group without adequate path filtering: functional-tests.yml (no path filtering). Add a check_paths filter job (see kerbside functional-tests.yml) or, only for workflows backing no required status check, trigger-level paths-ignore, excluding docs/** and the review-tracking files; mark deliberate exceptions with an "audit-ok: no-path-filter" comment
- **sfui** (Status): 1 expensive lane(s) triggered by pull_request or merge_group without adequate path filtering: functional-tests.yml (no path filtering). Add a check_paths filter job (see kerbside functional-tests.yml) or, only for workflows backing no required status check, trigger-level paths-ignore, excluding docs/** and the review-tracking files; mark deliberate exceptions with an "audit-ok: no-path-filter" comment

## export-repo-config

Criterion: [export-repo-config.md](/components/development/audits/export-repo-config/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | compliant | - |
| cloudgood | non-compliant | shakenfist/cloudgood#3 |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
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

- **cloudgood** (Status): Missing .github/workflows/export-repo-config.yml
- **kerbside-client** (Status): Missing .github/workflows/export-repo-config.yml
- **uncalibrated-sextant** (Status): Missing .github/workflows/export-repo-config.yml

## fuzz-nightly-reporting

Criterion: [fuzz-nightly-reporting.md](/components/development/audits/fuzz-nightly-reporting/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | N/A | - |
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
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | compliant | - |
| cloudgood | non-compliant | shakenfist/cloudgood#5 |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
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

- **cloudgood** (Status): Secret scanning not enabled; Secret scanning push protection not enabled
- **kerbside-client** (Status): Missing .github/workflows/codeql-analysis.yml; Secret scanning not enabled; Secret scanning push protection not enabled
- **uncalibrated-sextant** (Status): Missing .github/workflows/codeql-analysis.yml; Secret scanning not enabled; Secret scanning push protection not enabled

## llm-context-lint-ci

Criterion: [llm-context-lint-ci.md](/components/development/audits/llm-context-lint-ci/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#120 |
| cloudgood | non-compliant | shakenfist/cloudgood#8 |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | non-compliant | shakenfist/sfui#25 |
| shakenfist | compliant | - |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#6 |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **clingwrap** (Status): skillsaw does not run from .pre-commit-config.yaml or a CI workflow
- **cloudgood** (Status): skillsaw does not run from .pre-commit-config.yaml or a CI workflow
- **sfui** (Status): skillsaw does not run from .pre-commit-config.yaml or a CI workflow
- **uncalibrated-sextant** (Status): skillsaw does not run from .pre-commit-config.yaml or a CI workflow

## llm-context-lint

Criterion: [llm-context-lint.md](/components/development/audits/llm-context-lint/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | compliant | - |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
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
| client-python | non-compliant | shakenfist/client-python#406 |
| client-python-k3s | compliant | - |
| clingwrap | compliant | - |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| instar | compliant | - |
| kerbside | non-compliant | shakenfist/kerbside#475 |
| kerbside-client | compliant | - |
| kerbside-patches | non-compliant | shakenfist/kerbside-patches#1739 |
| library-utilities | compliant | - |
| occystrap | non-compliant | shakenfist/occystrap#144 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | non-compliant | shakenfist/shakenfist#4312 |
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **client-python** (Status): 1 agent instruction file is named for a single tool rather than AGENTS.md (CLAUDE.md); AGENTS.md is tracked beside it, so it is a second set of instructions loaded with equal authority: merge what is still true into AGENTS.md and delete the original
- **kerbside** (Status): 1 agent instruction file is named for a single tool rather than AGENTS.md (.claude/CLAUDE.md); AGENTS.md is tracked beside it, so it is a second set of instructions loaded with equal authority: merge what is still true into AGENTS.md and delete the original
- **kerbside-patches** (Status): 1 agent instruction file is named for a single tool rather than AGENTS.md (CLAUDE.md); AGENTS.md is tracked beside it, so it is a second set of instructions loaded with equal authority: merge what is still true into AGENTS.md and delete the original
- **occystrap** (Status): 1 agent instruction file is named for a single tool rather than AGENTS.md (CLAUDE.md); AGENTS.md is tracked beside it, so it is a second set of instructions loaded with equal authority: merge what is still true into AGENTS.md and delete the original
- **shakenfist** (Status): 1 agent instruction file is named for a single tool rather than AGENTS.md (CLAUDE.md); AGENTS.md is tracked beside it, so it is a second set of instructions loaded with equal authority: merge what is still true into AGENTS.md and delete the original

## llm-doc-structure

Criterion: [llm-doc-structure.md](/components/development/audits/llm-doc-structure/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | compliant | - |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
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

## llm-tooling

Criterion: [llm-tooling.md](/components/development/audits/llm-tooling/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | compliant | - |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
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
| agent-python | N/A | - |
| client-python | N/A | - |
| client-python-k3s | compliant | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | N/A | - |
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

## merge-queue-config

Criterion: [merge-queue-config.md](/components/development/audits/merge-queue-config/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| client-python | N/A | - |
| client-python-k3s | compliant | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
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
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | N/A | - |
| hunkydory | N/A | - |
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
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | compliant | - |
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
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | compliant | - |
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
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | compliant | - |
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
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | N/A | - |
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
| actions | N/A | - |
| agent-python | N/A | - |
| client-python | compliant | - |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | N/A | - |
| instar | non-compliant | shakenfist/instar#554 |
| kerbside | non-compliant | shakenfist/kerbside#452 |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#10 |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **instar** (Status): 1 of 1 incomplete master plan(s) do not end with a phase running PUSH-AUDIT.md, which the plan-push-audit-phase shared block requires; each is named with the fix it needs: PLAN-differencing.md (ends with a push audit phase that never names PUSH-AUDIT.md)
- **kerbside** (Status): 2 of 3 incomplete master plan(s) do not end with a phase running PUSH-AUDIT.md, which the plan-push-audit-phase shared block requires; each is named with the fix it needs: PLAN-use-case-docs.md (ends with a push audit phase that never names PUSH-AUDIT.md), PLAN-proxmox-source.md (ends with a push audit phase that never names PUSH-AUDIT.md)
- **uncalibrated-sextant** (Status): 5 of 5 incomplete master plan(s) do not end with a phase running PUSH-AUDIT.md, which the plan-push-audit-phase shared block requires; each is named with the fix it needs: PLAN-locked-bootloader.md (no push audit phase; phase 3 is "3. Iteration, documentation, inventory closeout"), PLAN-display-mode-keystrokes.md (no push audit phase; phase 3 is "3. Iteration against ryll display-mode-ui, documentation,..."), PLAN-audit-cleanup.md (no push audit phase; phase 3 is "3. Test coverage and release verification"), PLAN-visual-digest.md (no push audit phase; phase 3 is "3. Repaint integration, format spec, closeout"), PLAN-continuous-digest.md (no push audit phase; phase 3 is "3. Docs, decoder coordination, closeout"); 2 plan(s) with no phases this check can read, not judged: PLAN-language-probes.md, PLAN-headless-readback-bug.md

## plan-index

Criterion: [plan-index.md](/components/development/audits/plan-index/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | compliant | - |
| ryll | compliant | - |
| sfui | non-compliant | shakenfist/sfui#24 |
| shakenfist | compliant | - |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#9 |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **sfui** (Status): docs/plans/index.md is missing, so none of the 3 plan(s) in docs/plans/ are registered
- **uncalibrated-sextant** (Status): 7 status cell(s) outside the shared vocabulary (Proposed, Not started, In progress, Blocked, Complete, Abandoned, Superseded): Locked bootloader ("Complete (commits a7b261d through thi..."), Display-mode keystrokes ("Complete (commits 455d2b5 through thi..."), Audit cleanup ("Complete (commits 1482eb0 through thi..."), Visual on-screen digest ("Complete (commits 55844a5 through thi..."), Continuous multi-channel visual digest ("Complete (commits 8814fab through thi...") (+2 more)

## plan-phase-references

Criterion: [plan-phase-references.md](/components/development/audits/plan-phase-references/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| client-python | non-compliant | shakenfist/client-python#382 |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#58 |
| clingwrap | compliant | - |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| instar | non-compliant | shakenfist/instar#553 |
| kerbside | compliant | - |
| kerbside-client | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | non-compliant | shakenfist/private-ci#64 |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | non-compliant | shakenfist/shakenfist#4257 |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#8 |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **client-python** (Status): 1 plan phase reference(s) in documentation (describe the current behaviour, or link the master plan in docs/plans/ instead of citing a phase number): AGENTS.md:121
- **client-python-k3s** (Status): 4 plan phase reference(s) in documentation (describe the current behaviour, or link the master plan in docs/plans/ instead of citing a phase number): docs/library-api.md:98, docs/library-api.md:109, docs/library-api.md:230, docs/library-api.md:232
- **instar** (Status): 17 plan phase reference(s) in documentation (describe the current behaviour, or link the master plan in docs/plans/ instead of citing a phase number): docs/chain-discovery.md:207, docs/quirks.md:4199, docs/quirks.md:4208, docs/quirks.md:4216, docs/quirks.md:4217, docs/quirks.md:4218, docs/quirks.md:4449, docs/quirks.md:4493, docs/quirks.md:4505, docs/quirks.md:4508 (+7 more)
- **private-ci** (Status): 10 plan phase reference(s) in documentation (describe the current behaviour, or link the master plan in docs/plans/ instead of citing a phase number): docs/action-items-order.md:19, docs/dashboard-freshness.md:287, docs/gerrit-reviews.md:15, docs/gerrit-reviews.md:76, docs/gerrit-reviews.md:78, docs/gerrit-reviews.md:83, docs/gerrit-reviews.md:319, docs/gerrit-reviews.md:486, docs/gerrit-reviews.md:493, docs/gerrit-reviews.md:525
- **shakenfist** (Status): 3 plan phase reference(s) in documentation (describe the current behaviour, or link the master plan in docs/plans/ instead of citing a phase number): docs/developer_guide/ci.md:428, docs/developer_guide/ci.md:437, docs/operator_guide/capacity_refusals.md:116
- **uncalibrated-sextant** (Status): 22 plan phase reference(s) in documentation (describe the current behaviour, or link the master plan in docs/plans/ instead of citing a phase number): AGENTS.md:104, AGENTS.md:126, AGENTS.md:145, ARCHITECTURE.md:3, ARCHITECTURE.md:10, ARCHITECTURE.md:16, ARCHITECTURE.md:29, ARCHITECTURE.md:44, ARCHITECTURE.md:137, ARCHITECTURE.md:236 (+12 more)

## plan-source-references

Criterion: [plan-source-references.md](/components/development/audits/plan-source-references/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| client-python | non-compliant | shakenfist/client-python#403 |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | N/A | - |
| instar | non-compliant | shakenfist/instar#556 |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | compliant | - |
| ryll | compliant | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **client-python** (Status): 2 of 4 plan reference(s) in source or configuration do not resolve (update the path, or use an absolute https://github.com/... URL for a plan in another repository): shakenfist_client/apiclient.py:185 -> docs/plans/PLAN-transient-capacity-refusals-phase-04-retry-after.md, shakenfist_client/tests/test_client_apiclient.py:2320 -> docs/plans/PLAN-transient-capacity-refusals-phase-04-retry-after.md
- **instar** (Status): 2 of 267 plan reference(s) in source or configuration do not resolve (update the path, or use an absolute https://github.com/... URL for a plan in another repository): scripts/create-vhd-testdata.sh:46 -> docs/plans/PLAN-extra-coverage.md, scripts/create-vhd-testdata.sh:463 -> instar-testdata/docs/plans/PLAN-extra-coverage.md

## plan-template

Criterion: [plan-template.md](/components/development/audits/plan-template/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| client-python | N/A | - |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#66 |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | non-compliant | shakenfist/divergulent#116 |
| hunkydory | N/A | - |
| instar | non-compliant | shakenfist/instar#587 |
| kerbside | non-compliant | shakenfist/kerbside#471 |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | non-compliant | shakenfist/occystrap#142 |
| private-ci | non-compliant | shakenfist/private-ci#76 |
| ryll | non-compliant | shakenfist/ryll#392 |
| sfui | N/A | - |
| shakenfist | non-compliant | shakenfist/shakenfist#4299 |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#12 |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **client-python-k3s** (Status): missing shared block plan-phase-landing (copy it verbatim from templates/shared-blocks/plan-phase-landing.md in the development repository)
- **divergulent** (Status): missing shared block plan-phase-landing (copy it verbatim from templates/shared-blocks/plan-phase-landing.md in the development repository)
- **instar** (Status): missing shared block plan-phase-landing (copy it verbatim from templates/shared-blocks/plan-phase-landing.md in the development repository)
- **kerbside** (Status): missing shared block plan-phase-landing (copy it verbatim from templates/shared-blocks/plan-phase-landing.md in the development repository)
- **occystrap** (Status): missing shared block plan-phase-landing (copy it verbatim from templates/shared-blocks/plan-phase-landing.md in the development repository)
- **private-ci** (Status): missing shared block plan-phase-landing (copy it verbatim from templates/shared-blocks/plan-phase-landing.md in the development repository)
- **ryll** (Status): missing shared block plan-phase-landing (copy it verbatim from templates/shared-blocks/plan-phase-landing.md in the development repository)
- **shakenfist** (Status): missing shared block plan-phase-landing (copy it verbatim from templates/shared-blocks/plan-phase-landing.md in the development repository)
- **uncalibrated-sextant** (Status): missing shared block plan-status-vocabulary (copy it verbatim from templates/shared-blocks/plan-status-vocabulary.md in the development repository); missing shared block plan-push-audit-phase (copy it verbatim from templates/shared-blocks/plan-push-audit-phase.md in the development repository); missing shared block plan-phase-landing (copy it verbatim from templates/shared-blocks/plan-phase-landing.md in the development repository)

## push-audit

Criterion: [push-audit.md](/components/development/audits/push-audit/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| client-python | N/A | - |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#46 |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | non-compliant | shakenfist/divergulent#115 |
| hunkydory | compliant | - |
| instar | non-compliant | shakenfist/instar#586 |
| kerbside | non-compliant | shakenfist/kerbside#470 |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | non-compliant | shakenfist/occystrap#141 |
| private-ci | N/A | - |
| ryll | non-compliant | shakenfist/ryll#391 |
| sfui | non-compliant | shakenfist/sfui#15 |
| shakenfist | non-compliant | shakenfist/shakenfist#4298 |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#11 |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **client-python-k3s** (Status): missing shared block diagram-discipline (copy it verbatim from templates/shared-blocks/diagram-discipline.md in the development repository); missing shared block source-file-size (copy it verbatim from templates/shared-blocks/source-file-size.md in the development repository)
- **divergulent** (Status): missing shared block source-file-size (copy it verbatim from templates/shared-blocks/source-file-size.md in the development repository)
- **instar** (Status): missing shared block source-file-size (copy it verbatim from templates/shared-blocks/source-file-size.md in the development repository)
- **kerbside** (Status): missing shared block source-file-size (copy it verbatim from templates/shared-blocks/source-file-size.md in the development repository)
- **occystrap** (Status): missing shared block source-file-size (copy it verbatim from templates/shared-blocks/source-file-size.md in the development repository)
- **ryll** (Status): missing shared block source-file-size (copy it verbatim from templates/shared-blocks/source-file-size.md in the development repository)
- **sfui** (Status): missing shared block llm-doc-discipline (copy it verbatim from templates/shared-blocks/llm-doc-discipline.md in the development repository); missing shared block diagram-discipline (copy it verbatim from templates/shared-blocks/diagram-discipline.md in the development repository); missing shared block source-file-size (copy it verbatim from templates/shared-blocks/source-file-size.md in the development repository); missing shared block plan-phase-references (copy it verbatim from templates/shared-blocks/plan-phase-references.md in the development repository); missing shared block path-traversal-review (copy it verbatim from templates/shared-blocks/path-traversal-review.md in the development repository); missing shared block python-version-discipline (copy it verbatim from templates/shared-blocks/python-version-discipline.md in the development repository); missing shared block functional-test-coverage (copy it verbatim from templates/shared-blocks/functional-test-coverage.md in the development repository); AGENTS.md does not reference PUSH-AUDIT.md (an audit nothing points at does not get run)
- **shakenfist** (Status): missing shared block source-file-size (copy it verbatim from templates/shared-blocks/source-file-size.md in the development repository)
- **uncalibrated-sextant** (Status): missing shared block llm-doc-discipline (copy it verbatim from templates/shared-blocks/llm-doc-discipline.md in the development repository); missing shared block diagram-discipline (copy it verbatim from templates/shared-blocks/diagram-discipline.md in the development repository); missing shared block comment-proportion (copy it verbatim from templates/shared-blocks/comment-proportion.md in the development repository); missing shared block source-file-size (copy it verbatim from templates/shared-blocks/source-file-size.md in the development repository); missing shared block plan-phase-references (copy it verbatim from templates/shared-blocks/plan-phase-references.md in the development repository); missing shared block path-traversal-review (copy it verbatim from templates/shared-blocks/path-traversal-review.md in the development repository); missing shared block python-version-discipline (copy it verbatim from templates/shared-blocks/python-version-discipline.md in the development repository); missing shared block functional-test-coverage (copy it verbatim from templates/shared-blocks/functional-test-coverage.md in the development repository); AGENTS.md does not reference PUSH-AUDIT.md (an audit nothing points at does not get run)

## pyproject-usage

Criterion: [pyproject-usage.md](/components/development/audits/pyproject-usage/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | compliant | - |
| hunkydory | N/A | - |
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
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | compliant | - |
| hunkydory | N/A | - |
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
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#108 |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
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
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
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

## release-process

Criterion: [release-process.md](/components/development/audits/release-process/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | compliant | - |
| hunkydory | N/A | - |
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

## renovate-lockstep-groups

Criterion: [renovate-lockstep-groups.md](/components/development/audits/renovate-lockstep-groups/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | N/A | - |
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
| actions | compliant | - |
| agent-python | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#117 |
| cloudgood | non-compliant | shakenfist/cloudgood#2 |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | non-compliant | shakenfist/kerbside-client#2 |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#13 |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **clingwrap** (Status): renovate.json does not enable the pre-commit manager, so the hook revisions in .pre-commit-config.yaml are unmanaged and drift silently
- **cloudgood** (Status): Missing: .github/workflows/renovate.yml, renovate.json
- **kerbside-client** (Status): Missing: .github/workflows/renovate.yml, renovate.json
- **uncalibrated-sextant** (Status): Missing: .github/workflows/renovate.yml, renovate.json

## review-coverage

Criterion: [review-coverage.md](/components/development/audits/review-coverage/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | non-compliant | shakenfist/actions#95 |
| agent-python | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | N/A | - |
| hunkydory | compliant | - |
| instar | N/A | - |
| kerbside | non-compliant | shakenfist/kerbside#227 |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | non-compliant | shakenfist/ryll#403 |
| sfui | N/A | - |
| shakenfist | N/A | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **actions** (Status): 89 of 129 in-scope files reviewed at HEAD; 40 need review (threshold 5)
- **kerbside** (Status): 98 of 238 in-scope files reviewed at HEAD; 140 need review (threshold 5)
- **ryll** (Status): 206 of 214 in-scope files reviewed at HEAD; 8 need review (threshold 5)

## review-scope-completeness

Criterion: [review-scope-completeness.md](/components/development/audits/review-scope-completeness/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | N/A | - |
| hunkydory | compliant | - |
| instar | N/A | - |
| kerbside | compliant | - |
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

## rust-unwrap-lint

Criterion: [rust-unwrap-lint.md](/components/development/audits/rust-unwrap-lint/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | N/A | - |
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

## scope-coverage

Criterion: [scope-coverage.md](/components/development/audits/scope-coverage/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | N/A | - |
| hunkydory | N/A | - |
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
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#111 |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| hunkydory | compliant | - |
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
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#18 |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **clingwrap** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
- **uncalibrated-sextant** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow

## security-sanitization

Criterion: [security-sanitization.md](/components/development/audits/security-sanitization/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | N/A | - |
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
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | N/A | - |
| instar | N/A | - |
| kerbside | non-compliant | shakenfist/kerbside#445 |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | non-compliant | shakenfist/private-ci#58 |
| ryll | non-compliant | shakenfist/ryll#382 |
| sfui | N/A | - |
| shakenfist | N/A | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **kerbside** (Status): kerbside/api/static/sfui: 4 commit(s) behind canonical; re-run tools/vendor.sh from an up to date sfui checkout
- **private-ci** (Status): conductor/static/sfui: 6 commit(s) behind canonical; re-run tools/vendor.sh from an up to date sfui checkout
- **ryll** (Status): ryll/src/web/assets/sfui: 6 commit(s) behind canonical; re-run tools/vendor.sh from an up to date sfui checkout

## undeclared-direct-dependency

Criterion: [undeclared-direct-dependency.md](/components/development/audits/undeclared-direct-dependency/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| hunkydory | N/A | - |
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

## unused-declared-dependency

Criterion: [unused-declared-dependency.md](/components/development/audits/unused-declared-dependency/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | compliant | - |
| client-python | non-compliant | shakenfist/client-python#383 |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#50 |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | compliant | - |
| hunkydory | N/A | - |
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
- **client-python-k3s** (Status): Declared but never imported: prettytable (pyproject.toml:33). Remove each, or record why it is installed with a "# not-imported: <name> -- <reason>" comment in the dependencies array

## version-file-gitignore

Criterion: [version-file-gitignore.md](/components/development/audits/version-file-gitignore/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#106 |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | compliant | - |
| hunkydory | N/A | - |
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

| Project | flake8wrap | Runners | Static tags | VM size | Permissions | Linting | devpi fallback | devpi IP | Review marks | Issue |
|---------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|
| actions | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | compliant | - |
| agent-python | compliant | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | - |
| client-python | compliant | compliant | compliant | non-compliant | compliant | compliant | N/A | compliant | N/A | shakenfist/client-python#378 |
| client-python-k3s | compliant | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | - |
| clingwrap | compliant | compliant | compliant | non-compliant | compliant | compliant | N/A | compliant | N/A | shakenfist/clingwrap#125 |
| cloudgood | N/A | N/A | N/A | N/A | N/A | compliant | N/A | N/A | N/A | - |
| development | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | - |
| divergulent | compliant | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | - |
| hunkydory | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | - |
| instar | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | - |
| kerbside | compliant | compliant | compliant | compliant | compliant | compliant | compliant | compliant | compliant | - |
| kerbside-client | non-compliant | N/A | N/A | N/A | N/A | non-compliant | N/A | N/A | N/A | shakenfist/kerbside-client#4, shakenfist/kerbside-client#6 |
| kerbside-patches | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | - |
| library-utilities | compliant | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | - |
| occystrap | compliant | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | - |
| private-ci | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | - |
| ryll | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | - |
| sfui | N/A | compliant | compliant | compliant | compliant | compliant | compliant | compliant | N/A | - |
| shakenfist | compliant | compliant | compliant | compliant | compliant | compliant | compliant | compliant | N/A | - |
| uncalibrated-sextant | N/A | non-compliant | compliant | compliant | non-compliant | compliant | N/A | compliant | N/A | shakenfist/uncalibrated-sextant#15, shakenfist/uncalibrated-sextant#17 |
| visual-digest-rust | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | - |

Details for non-compliant projects:

- **client-python** (VM size): 3 "vm" runner job(s) naming no size: code-formatting.yml:19 (self-hosted, vm), functional-tests.yml:23 (self-hosted, vm), supply-chain.yml:81 (self-hosted, vm). The conductor takes the runner size from the labels and falls back to the first CI_SIZES entry -- "xs", one vCPU and 2048 MB -- when it finds none, so an omitted size is a silent downgrade to the smallest runner rather than a free choice. Add the size the job actually wants (xs/s/m/l/xl, or m-bigdisk/xl-bigdisk when the job needs the disk); "xs" is a valid answer stated explicitly. A job which genuinely cannot name one marks the line "audit-ok: vm-runner-size" with the reason
- **clingwrap** (VM size): 1 "vm" runner job(s) naming no size: functional-tests.yml:22 (self-hosted, vm, debian-13). The conductor takes the runner size from the labels and falls back to the first CI_SIZES entry -- "xs", one vCPU and 2048 MB -- when it finds none, so an omitted size is a silent downgrade to the smallest runner rather than a free choice. Add the size the job actually wants (xs/s/m/l/xl, or m-bigdisk/xl-bigdisk when the job needs the disk); "xs" is a valid answer stated explicitly. A job which genuinely cannot name one marks the line "audit-ok: vm-runner-size" with the reason
- **kerbside-client** (flake8wrap): Missing shellcheck disable=SC2086 directive
- **kerbside-client** (Linting): Missing .pre-commit-config.yaml
- **uncalibrated-sextant** (Runners): 1 unmarked GitHub-hosted runner reference(s): pre-commit.yml:10 (ubuntu-latest). Move to a self-hosted runner, or mark deliberate exceptions with an "audit-ok: github-hosted-runner" comment
- **uncalibrated-sextant** (Permissions): 1 workflow(s) missing top-level permissions: pre-commit.yml

## Criteria with no automated check

These criteria are written down and judged by a person, so they have no table above. Each says why in its own page:

- [test-coverage.md](/components/development/audits/test-coverage/)
<!-- consistency-audit:end -->
