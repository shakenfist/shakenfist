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
*Generated 2026-09-05T10:11:43.218556+00:00 from `scripts/audit-check.py`; do not edit.*

## ci-review-automation

Criterion: [ci-review-automation.md](/components/development/audits/ci-review-automation/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | non-compliant | shakenfist/agent-python#126 |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#121 |
| cloudgood | non-compliant | shakenfist/cloudgood#1 |
| development | compliant | - |
| divergulent | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | non-compliant | shakenfist/kerbside-client#5 |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | non-compliant | shakenfist/occystrap#120 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | non-compliant | shakenfist/sfui#26 |
| shakenfist | non-compliant | shakenfist/shakenfist#3314 |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#16 |
| visual-digest-rust | non-compliant | shakenfist/visual-digest-rust#16 |

Details for non-compliant projects:

- **agent-python** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml); it is unused, and its workflow holds contents: write on the pull request branch
- **clingwrap** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py, tools/review-schema.json); it is unused, and its workflow holds contents: write on the pull request branch
- **cloudgood** (Status): Missing workflows: pr-re-review.yml
- **kerbside-client** (Status): Missing pr-re-review.yml; Missing pr-retest.yml; No workflow uses shared action review-pr-with-claude@main
- **occystrap** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py, tools/review-schema.json); it is unused, and its workflow holds contents: write on the pull request branch
- **sfui** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py); it is unused, and its workflow holds contents: write on the pull request branch
- **shakenfist** (Status): Missing pr-retest.yml; pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py, tools/review-schema.json); it is unused, and its workflow holds contents: write on the pull request branch
- **uncalibrated-sextant** (Status): Missing pr-re-review.yml; Missing pr-retest.yml; No workflow uses shared action review-pr-with-claude@main
- **visual-digest-rust** (Status): Missing pr-re-review.yml; Missing pr-retest.yml; No workflow uses shared action review-pr-with-claude@main

## console-logging

Criterion: [console-logging.md](/components/development/audits/console-logging/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | non-compliant | shakenfist/agent-python#128 |
| client-python | compliant | - |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| instar | N/A | - |
| kerbside | N/A | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | non-compliant | shakenfist/occystrap#124 |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **agent-python** (Status): 1 of 1 console entry point(s) calling setup_console() do not configure the root logger -- shakenfist_agent/main.py: missing logging.basicConfig() (INFO from every other module reaches a root logger with no handler and is dropped); propagate = False on its own logger (its own lines are emitted twice once root has a handler)
- **occystrap** (Status): 1 of 1 console entry point(s) calling setup_console() do not configure the root logger -- occystrap/main.py: missing logging.basicConfig() (INFO from every other module reaches a root logger with no handler and is dropped); propagate = False on its own logger (its own lines are emitted twice once root has a handler)

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
| visual-digest-rust | non-compliant | shakenfist/visual-digest-rust#21 |

Details for non-compliant projects:

- **kerbside-client** (Status): Delete branch on merge is not enabled
- **uncalibrated-sextant** (Status): Delete branch on merge is not enabled
- **visual-digest-rust** (Status): Delete branch on merge is not enabled

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
| instar | non-compliant | shakenfist/instar#536 |
| kerbside | compliant | - |
| kerbside-client | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | non-compliant | shakenfist/occystrap#127 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **clingwrap** (Status): 1 diagram(s) drawn in ASCII rather than mermaid (convert them, or mark a block that is genuinely better drawn by hand with an "audit-ok: diagram-format" comment above the fence): ARCHITECTURE.md:21
- **cloudgood** (Status): 1 diagram(s) drawn in ASCII rather than mermaid (convert them, or mark a block that is genuinely better drawn by hand with an "audit-ok: diagram-format" comment above the fence): docs/memory-mapped-devices.md:729
- **instar** (Status): 13 diagram(s) drawn in ASCII rather than mermaid (convert them, or mark a block that is genuinely better drawn by hand with an "audit-ok: diagram-format" comment above the fence): ARCHITECTURE.md:21, docs/format-detection-safety.md:48, docs/technology-primer.md:988, docs/prototypes/kvm-hello-world.md:18, docs/prototypes/kvm-hello-world2.md:22, docs/prototypes/virtio-block.md:49, docs/prototypes/virtio-block.md:96, docs/prototypes/virtio-block2.md:25, docs/prototypes/virtio-block3.md:26, docs/prototypes/virtio-block5.md:65 (+3 more)
- **occystrap** (Status): 1 diagram(s) drawn in ASCII rather than mermaid (convert them, or mark a block that is genuinely better drawn by hand with an "audit-ok: diagram-format" comment above the fence): docs/internals.md:30

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

- **cloudgood** (Status): 2 relative link(s) in docs/ that do not resolve to a file inside docs/ (use absolute https://github.com/... URLs, which survive the docs site import): docs/index.md -> more-fundamentals.md, docs/virtualization-history.md -> more-fundamentals.md
- **uncalibrated-sextant** (Status): 67 relative link(s) in docs/ that do not resolve to a file inside docs/ (use absolute https://github.com/... URLs, which survive the docs site import): docs/plans/PLAN-audit-cleanup-phase-02-structural.md -> ../../src/bootloader.rs, docs/plans/PLAN-audit-cleanup-phase-02-structural.md -> ../../src/renderer/mod.rs, docs/plans/PLAN-audit-cleanup-phase-02-structural.md -> ../../src/scene.rs, docs/plans/PLAN-audit-cleanup-phase-03-tests.md -> ../../Makefile, docs/plans/PLAN-audit-cleanup-phase-03-tests.md -> ../../scripts/screenshot.sh, docs/plans/PLAN-audit-cleanup-phase-03-tests.md -> ../../scripts/verify-release.sh, docs/plans/PLAN-audit-cleanup-phase-03-tests.md -> ../../src/scene.rs, docs/plans/PLAN-audit-cleanup.md -> ../../AGENTS.md, docs/plans/PLAN-audit-cleanup.md -> ../../ARCHITECTURE.md, docs/plans/PLAN-audit-cleanup.md -> ../../PUSH-AUDIT.md (+57 more)

## expensive-lane-path-filter

Criterion: [expensive-lane-path-filter.md](/components/development/audits/expensive-lane-path-filter/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | non-compliant | shakenfist/agent-python#123 |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#118 |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | non-compliant | shakenfist/occystrap#113 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | non-compliant | shakenfist/sfui#14 |
| shakenfist | compliant | - |
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | non-compliant | shakenfist/visual-digest-rust#17 |

Details for non-compliant projects:

- **agent-python** (Status): 1 expensive lane(s) triggered by pull_request or merge_group without adequate path filtering: functional-tests.yml (no path filtering). Add a check_paths filter job (see kerbside functional-tests.yml) or, only for workflows backing no required status check, trigger-level paths-ignore, excluding docs/** and the review-tracking files; mark deliberate exceptions with an "audit-ok: no-path-filter" comment
- **clingwrap** (Status): 1 expensive lane(s) triggered by pull_request or merge_group without adequate path filtering: functional-tests.yml (no path filtering). Add a check_paths filter job (see kerbside functional-tests.yml) or, only for workflows backing no required status check, trigger-level paths-ignore, excluding docs/** and the review-tracking files; mark deliberate exceptions with an "audit-ok: no-path-filter" comment
- **occystrap** (Status): 2 expensive lane(s) triggered by pull_request or merge_group without adequate path filtering: functional-tests.yml (no path filtering), python-unit-tests.yml (no path filtering). Add a check_paths filter job (see kerbside functional-tests.yml) or, only for workflows backing no required status check, trigger-level paths-ignore, excluding docs/** and the review-tracking files; mark deliberate exceptions with an "audit-ok: no-path-filter" comment
- **sfui** (Status): 1 expensive lane(s) triggered by pull_request or merge_group without adequate path filtering: functional-tests.yml (no path filtering). Add a check_paths filter job (see kerbside functional-tests.yml) or, only for workflows backing no required status check, trigger-level paths-ignore, excluding docs/** and the review-tracking files; mark deliberate exceptions with an "audit-ok: no-path-filter" comment
- **visual-digest-rust** (Status): 1 expensive lane(s) triggered by pull_request or merge_group without adequate path filtering: ci.yml (no path filtering). Add a check_paths filter job (see kerbside functional-tests.yml) or, only for workflows backing no required status check, trigger-level paths-ignore, excluding docs/** and the review-tracking files; mark deliberate exceptions with an "audit-ok: no-path-filter" comment

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
| visual-digest-rust | non-compliant | shakenfist/visual-digest-rust#19 |

Details for non-compliant projects:

- **cloudgood** (Status): Missing .github/workflows/export-repo-config.yml
- **kerbside-client** (Status): Missing .github/workflows/export-repo-config.yml
- **uncalibrated-sextant** (Status): Missing .github/workflows/export-repo-config.yml
- **visual-digest-rust** (Status): Missing .github/workflows/export-repo-config.yml

## github-security

Criterion: [github-security.md](/components/development/audits/github-security/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | non-compliant | shakenfist/agent-python#81 |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | compliant | - |
| cloudgood | non-compliant | shakenfist/cloudgood#5 |
| development | compliant | - |
| divergulent | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | non-compliant | shakenfist/kerbside-client#8 |
| kerbside-patches | compliant | - |
| library-utilities | non-compliant | shakenfist/library-utilities#36 |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | non-compliant | shakenfist/shakenfist#3056 |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#20 |
| visual-digest-rust | non-compliant | shakenfist/visual-digest-rust#20 |

Details for non-compliant projects:

- **agent-python** (Status): Secret scanning not enabled; Secret scanning push protection not enabled
- **cloudgood** (Status): Secret scanning not enabled; Secret scanning push protection not enabled
- **kerbside-client** (Status): Missing .github/workflows/codeql-analysis.yml; Secret scanning not enabled; Secret scanning push protection not enabled
- **library-utilities** (Status): Secret scanning not enabled; Secret scanning push protection not enabled
- **shakenfist** (Status): Secret scanning not enabled; Secret scanning push protection not enabled
- **uncalibrated-sextant** (Status): Missing .github/workflows/codeql-analysis.yml; Secret scanning not enabled; Secret scanning push protection not enabled
- **visual-digest-rust** (Status): Missing .github/workflows/codeql-analysis.yml; Secret scanning not enabled; Secret scanning push protection not enabled

## llm-context-lint-ci

Criterion: [llm-context-lint-ci.md](/components/development/audits/llm-context-lint-ci/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | non-compliant | shakenfist/agent-python#125 |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#120 |
| cloudgood | non-compliant | shakenfist/cloudgood#8 |
| development | compliant | - |
| divergulent | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | non-compliant | shakenfist/occystrap#119 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | non-compliant | shakenfist/sfui#25 |
| shakenfist | non-compliant | shakenfist/shakenfist#3832 |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#6 |
| visual-digest-rust | non-compliant | shakenfist/visual-digest-rust#9 |

Details for non-compliant projects:

- **agent-python** (Status): skillsaw does not run from .pre-commit-config.yaml or a CI workflow
- **clingwrap** (Status): skillsaw does not run from .pre-commit-config.yaml or a CI workflow
- **cloudgood** (Status): skillsaw does not run from .pre-commit-config.yaml or a CI workflow
- **occystrap** (Status): skillsaw does not run from .pre-commit-config.yaml or a CI workflow
- **sfui** (Status): skillsaw does not run from .pre-commit-config.yaml or a CI workflow
- **shakenfist** (Status): skillsaw does not run from .pre-commit-config.yaml or a CI workflow
- **uncalibrated-sextant** (Status): skillsaw does not run from .pre-commit-config.yaml or a CI workflow
- **visual-digest-rust** (Status): skillsaw does not run from .pre-commit-config.yaml or a CI workflow

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
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | non-compliant | shakenfist/occystrap#118 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **occystrap** (Status): Markdown that will never load as a skill: .claude/skills/documentation-updates.md, .claude/skills/pr-preparation.md, .claude/skills/testing-discipline.md

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
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | non-compliant | shakenfist/ryll#356 |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | non-compliant | shakenfist/visual-digest-rust#8 |

Details for non-compliant projects:

- **ryll** (Status): AGENTS.md is 307 lines / 2118 words (limits: 300 lines, 2500 words); move detail into docs/ and leave a summary and a link
- **visual-digest-rust** (Status): AGENTS.md and ARCHITECTURE.md share the headings "feature flag matrix"; give each fact one home and link to it from the other file

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
| instar | N/A | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | non-compliant | shakenfist/ryll#337 |
| sfui | N/A | - |
| shakenfist | non-compliant | shakenfist/shakenfist#3979 |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **ryll** (Status): mermaid diagrams are not linted: missing tools/mermaid-lint.sh and a CI workflow that runs it (copy templates/mermaid-lint/ from the development repository)
- **shakenfist** (Status): mermaid diagrams are not linted: missing tools/mermaid-lint.sh and a CI workflow that runs it (copy templates/mermaid-lint/ from the development repository)

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
| divergulent | non-compliant | shakenfist/divergulent#103 |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | non-compliant | shakenfist/occystrap#129 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | N/A | - |
| shakenfist | non-compliant | shakenfist/shakenfist#4063 |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#10 |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **divergulent** (Status): 1 of 1 incomplete master plan(s) do not end with a phase running PUSH-AUDIT.md, which the plan-push-audit-phase shared block requires; each is named with the fix it needs: PLAN-release-1.0.md (no push audit phase; phase 8 is "Builder robustness and publish safety"); 1 plan(s) with no phases this check can read, not judged: PLAN-curation-cli-ergonomics.md
- **occystrap** (Status): 1 of 1 incomplete master plan(s) do not end with a phase running PUSH-AUDIT.md, which the plan-push-audit-phase shared block requires; each is named with the fix it needs: PLAN-quay-label-search.md (no push audit phase; phase 5 is "5. Filter by tag age (since parameter)"); 1 plan(s) with no phases this check can read, not judged: PLAN-info-check.md
- **shakenfist** (Status): 2 of 18 incomplete master plan(s) do not end with a phase running PUSH-AUDIT.md, which the plan-push-audit-phase shared block requires; each is named with the fix it needs: PLAN-ci-cloud-sizing.md (no push audit phase; phase 6 is "6. Documentation and downstream propagation"), PLAN-kerbside-vdi-tokens.md (push audit phase is not last, so phase 11 ("11. Close out the post-completion defects (#4003, #4009)") is unaudited; move the audit phase after it); 1 plan(s) with no phases this check can read, not judged: PLAN-netserv.md
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
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | non-compliant | shakenfist/occystrap#116 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | non-compliant | shakenfist/sfui#24 |
| shakenfist | compliant | - |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#9 |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **occystrap** (Status): index has no plan table (it must list plans in a table led by Date and Plan columns, not as prose or a bullet list); 4 master plan(s) not listed in the index: PLAN-make-the-speed.md, PLAN-post-write-verification.md, PLAN-registry-proxy.md, PLAN-structured-logging.md
- **sfui** (Status): docs/plans/index.md is missing, so none of the 3 plan(s) in docs/plans/ are registered
- **uncalibrated-sextant** (Status): 7 status cell(s) outside the shared vocabulary (Proposed, Not started, In progress, Blocked, Complete, Abandoned, Superseded): Locked bootloader ("Complete (commits a7b261d through thi..."), Display-mode keystrokes ("Complete (commits 455d2b5 through thi..."), Audit cleanup ("Complete (commits 1482eb0 through thi..."), Visual on-screen digest ("Complete (commits 55844a5 through thi..."), Continuous multi-channel visual digest ("Complete (commits 8814fab through thi...") (+2 more)

## plan-phase-references

Criterion: [plan-phase-references.md](/components/development/audits/plan-phase-references/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| client-python | non-compliant | shakenfist/client-python#382 |
| client-python-k3s | compliant | - |
| clingwrap | compliant | - |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | non-compliant | shakenfist/shakenfist#3732 |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#8 |
| visual-digest-rust | non-compliant | shakenfist/visual-digest-rust#11 |

Details for non-compliant projects:

- **client-python** (Status): 1 plan phase reference(s) in documentation (describe the current behaviour, or link the master plan in docs/plans/ instead of citing a phase number): AGENTS.md:121
- **shakenfist** (Status): 24 plan phase reference(s) in documentation (describe the current behaviour, or link the master plan in docs/plans/ instead of citing a phase number): ARCHITECTURE.md:198, docs/developer_guide/ci.md:70, docs/developer_guide/ci.md:132, docs/developer_guide/ci.md:185, docs/developer_guide/ci.md:236, docs/developer_guide/ci.md:237, docs/developer_guide/database_internals.md:340, docs/developer_guide/database_internals.md:344, docs/developer_guide/subsystem_internals.md:51, docs/developer_guide/subsystem_internals.md:110 (+14 more)
- **uncalibrated-sextant** (Status): 22 plan phase reference(s) in documentation (describe the current behaviour, or link the master plan in docs/plans/ instead of citing a phase number): AGENTS.md:104, AGENTS.md:126, AGENTS.md:145, ARCHITECTURE.md:3, ARCHITECTURE.md:10, ARCHITECTURE.md:16, ARCHITECTURE.md:29, ARCHITECTURE.md:44, ARCHITECTURE.md:137, ARCHITECTURE.md:236 (+12 more)
- **visual-digest-rust** (Status): 7 plan phase reference(s) in documentation (describe the current behaviour, or link the master plan in docs/plans/ instead of citing a phase number): README.md:25, README.md:30, README.md:107, AGENTS.md:99, AGENTS.md:101, ARCHITECTURE.md:22, ARCHITECTURE.md:68

## plan-source-references

Criterion: [plan-source-references.md](/components/development/audits/plan-source-references/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | non-compliant | shakenfist/actions#43 |
| agent-python | N/A | - |
| client-python | compliant | - |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
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
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | non-compliant | shakenfist/visual-digest-rust#12 |

Details for non-compliant projects:

- **actions** (Status): 3 of 3 plan reference(s) in source or configuration do not resolve (update the path, or use an absolute https://github.com/... URL for a plan in another repository): .github/workflows/smoke-cluster.yml:268 -> docs/plans/PLAN-ci-cloud-sizing-phase-01-headroom-probe.md, tools/ci_headroom_collect.sh:14 -> docs/plans/PLAN-ci-cloud-sizing-phase-01-headroom-probe.md, tools/ci_headroom_launch.sh:13 -> docs/plans/PLAN-ci-cloud-sizing-phase-01-headroom-probe.md
- **visual-digest-rust** (Status): 1 of 1 plan reference(s) in source or configuration do not resolve (update the path, or use an absolute https://github.com/... URL for a plan in another repository): shakenfist-visual-digest/tests/qr.rs:7 -> PLAN-test-harness-phase-01-digest-crate.md

## plan-template

Criterion: [plan-template.md](/components/development/audits/plan-template/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| client-python | N/A | - |
| client-python-k3s | compliant | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | non-compliant | shakenfist/occystrap#117 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | N/A | - |
| shakenfist | non-compliant | shakenfist/shakenfist#3892 |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#12 |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **occystrap** (Status): missing shared block plan-status-vocabulary (copy it verbatim from templates/shared-blocks/plan-status-vocabulary.md in the development repository); missing shared block plan-push-audit-phase (copy it verbatim from templates/shared-blocks/plan-push-audit-phase.md in the development repository)
- **shakenfist** (Status): missing shared block plan-push-audit-phase (copy it verbatim from templates/shared-blocks/plan-push-audit-phase.md in the development repository)
- **uncalibrated-sextant** (Status): missing shared block plan-status-vocabulary (copy it verbatim from templates/shared-blocks/plan-status-vocabulary.md in the development repository); missing shared block plan-push-audit-phase (copy it verbatim from templates/shared-blocks/plan-push-audit-phase.md in the development repository)

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
| divergulent | compliant | - |
| instar | non-compliant | shakenfist/instar#491 |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | non-compliant | shakenfist/occystrap#110 |
| private-ci | N/A | - |
| ryll | non-compliant | shakenfist/ryll#323 |
| sfui | non-compliant | shakenfist/sfui#15 |
| shakenfist | non-compliant | shakenfist/shakenfist#3911 |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#11 |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **client-python-k3s** (Status): missing shared block diagram-discipline (copy it verbatim from templates/shared-blocks/diagram-discipline.md in the development repository)
- **instar** (Status): missing shared block diagram-discipline (copy it verbatim from templates/shared-blocks/diagram-discipline.md in the development repository)
- **occystrap** (Status): missing shared block llm-doc-discipline (copy it verbatim from templates/shared-blocks/llm-doc-discipline.md in the development repository); missing shared block diagram-discipline (copy it verbatim from templates/shared-blocks/diagram-discipline.md in the development repository); missing shared block plan-phase-references (copy it verbatim from templates/shared-blocks/plan-phase-references.md in the development repository); missing shared block path-traversal-review (copy it verbatim from templates/shared-blocks/path-traversal-review.md in the development repository); missing shared block python-version-discipline (copy it verbatim from templates/shared-blocks/python-version-discipline.md in the development repository); missing shared block functional-test-coverage (copy it verbatim from templates/shared-blocks/functional-test-coverage.md in the development repository); AGENTS.md does not reference PUSH-AUDIT.md (an audit nothing points at does not get run)
- **ryll** (Status): missing shared block diagram-discipline (copy it verbatim from templates/shared-blocks/diagram-discipline.md in the development repository); missing shared block path-traversal-review (copy it verbatim from templates/shared-blocks/path-traversal-review.md in the development repository); missing shared block python-version-discipline (copy it verbatim from templates/shared-blocks/python-version-discipline.md in the development repository); missing shared block functional-test-coverage (copy it verbatim from templates/shared-blocks/functional-test-coverage.md in the development repository)
- **sfui** (Status): missing shared block llm-doc-discipline (copy it verbatim from templates/shared-blocks/llm-doc-discipline.md in the development repository); missing shared block diagram-discipline (copy it verbatim from templates/shared-blocks/diagram-discipline.md in the development repository); missing shared block plan-phase-references (copy it verbatim from templates/shared-blocks/plan-phase-references.md in the development repository); missing shared block path-traversal-review (copy it verbatim from templates/shared-blocks/path-traversal-review.md in the development repository); missing shared block python-version-discipline (copy it verbatim from templates/shared-blocks/python-version-discipline.md in the development repository); missing shared block functional-test-coverage (copy it verbatim from templates/shared-blocks/functional-test-coverage.md in the development repository); AGENTS.md does not reference PUSH-AUDIT.md (an audit nothing points at does not get run)
- **shakenfist** (Status): missing shared block diagram-discipline (copy it verbatim from templates/shared-blocks/diagram-discipline.md in the development repository); missing shared block path-traversal-review (copy it verbatim from templates/shared-blocks/path-traversal-review.md in the development repository); missing shared block python-version-discipline (copy it verbatim from templates/shared-blocks/python-version-discipline.md in the development repository); missing shared block functional-test-coverage (copy it verbatim from templates/shared-blocks/functional-test-coverage.md in the development repository)
- **uncalibrated-sextant** (Status): missing shared block llm-doc-discipline (copy it verbatim from templates/shared-blocks/llm-doc-discipline.md in the development repository); missing shared block diagram-discipline (copy it verbatim from templates/shared-blocks/diagram-discipline.md in the development repository); missing shared block comment-proportion (copy it verbatim from templates/shared-blocks/comment-proportion.md in the development repository); missing shared block plan-phase-references (copy it verbatim from templates/shared-blocks/plan-phase-references.md in the development repository); missing shared block path-traversal-review (copy it verbatim from templates/shared-blocks/path-traversal-review.md in the development repository); missing shared block python-version-discipline (copy it verbatim from templates/shared-blocks/python-version-discipline.md in the development repository); missing shared block functional-test-coverage (copy it verbatim from templates/shared-blocks/functional-test-coverage.md in the development repository); AGENTS.md does not reference PUSH-AUDIT.md (an audit nothing points at does not get run)

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
| agent-python | non-compliant | shakenfist/agent-python#107 |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#108 |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
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

- **agent-python** (Status): 5 relative link target(s) in README.md (use absolute URLs so the README renders off the repo landing page): AGENTS.md, ARCHITECTURE.md, docs/developer-guide.md, docs/index.md, docs/protocol.md
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
| visual-digest-rust | non-compliant | shakenfist/visual-digest-rust#10 |

Details for non-compliant projects:

- **visual-digest-rust** (Status): README.md has no link into docs/ despite a docs/ directory existing; add curated links to the detailed documentation

## release-process

Criterion: [release-process.md](/components/development/audits/release-process/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | non-compliant | shakenfist/agent-python#135 |
| client-python | non-compliant | shakenfist/client-python#391 |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#54 |
| clingwrap | non-compliant | shakenfist/clingwrap#131 |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | non-compliant | shakenfist/divergulent#106 |
| instar | N/A | - |
| kerbside | non-compliant | shakenfist/kerbside#408 |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | non-compliant | shakenfist/occystrap#132 |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | non-compliant | shakenfist/shakenfist#4082 |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **agent-python** (Status): the github-release job downloads artifacts without "name:" or "merge-multiple: true", so the files do not land where its "files:" glob looks and the release is published empty; add "name: dist" and "path: dist/" as the publish job does; the github-release job attaches release assets without "fail_on_unmatched_files: true", so a glob which matches nothing is a warning and an empty release still reports success
- **client-python** (Status): the github-release job downloads artifacts without "name:" or "merge-multiple: true", so the files do not land where its "files:" glob looks and the release is published empty; add "name: dist" and "path: dist/" as the publish job does; the github-release job attaches release assets without "fail_on_unmatched_files: true", so a glob which matches nothing is a warning and an empty release still reports success
- **client-python-k3s** (Status): the github-release job downloads artifacts without "name:" or "merge-multiple: true", so the files do not land where its "files:" glob looks and the release is published empty; add "name: dist" and "path: dist/" as the publish job does; the github-release job attaches release assets without "fail_on_unmatched_files: true", so a glob which matches nothing is a warning and an empty release still reports success
- **clingwrap** (Status): the github-release job downloads artifacts without "name:" or "merge-multiple: true", so the files do not land where its "files:" glob looks and the release is published empty; add "name: dist" and "path: dist/" as the publish job does; the github-release job attaches release assets without "fail_on_unmatched_files: true", so a glob which matches nothing is a warning and an empty release still reports success
- **divergulent** (Status): the github-release job downloads artifacts without "name:" or "merge-multiple: true", so the files do not land where its "files:" glob looks and the release is published empty; add "name: dist" and "path: dist/" as the publish job does; the github-release job attaches release assets without "fail_on_unmatched_files: true", so a glob which matches nothing is a warning and an empty release still reports success
- **kerbside** (Status): the github-release job downloads artifacts without "name:" or "merge-multiple: true", so the files do not land where its "files:" glob looks and the release is published empty; add "name: dist" and "path: dist/" as the publish job does; the github-release job attaches release assets without "fail_on_unmatched_files: true", so a glob which matches nothing is a warning and an empty release still reports success
- **occystrap** (Status): the github-release job downloads artifacts without "name:" or "merge-multiple: true", so the files do not land where its "files:" glob looks and the release is published empty; add "name: dist" and "path: dist/" as the publish job does; the github-release job attaches release assets without "fail_on_unmatched_files: true", so a glob which matches nothing is a warning and an empty release still reports success
- **shakenfist** (Status): the github-release job downloads artifacts without "name:" or "merge-multiple: true", so the files do not land where its "files:" glob looks and the release is published empty; add "name: dist" and "path: dist/" as the publish job does; the github-release job attaches release assets without "fail_on_unmatched_files: true", so a glob which matches nothing is a warning and an empty release still reports success; release.yml can be started by hand but its publishing jobs are not confined to tags: sign-tag, publish-pypi, publish-collection, github-release lack "if: startsWith(github.ref, 'refs/tags/v')", so a manual run on a branch force-pushes a "refs/tags/refs/heads/<branch>" tag and proceeds to publish

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
| instar | N/A | - |
| kerbside | non-compliant | shakenfist/kerbside#401 |
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

- **kerbside** (Status): Not grouped for Renovate: oslo (oslo.concurrency, oslo.config, oslo.i18n, oslo.utils) -- the OpenStack oslo libraries. Add a packageRules entry with a groupName covering every member, unrestricted by matchUpdateTypes

## renovate

Criterion: [renovate.md](/components/development/audits/renovate/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | non-compliant | shakenfist/agent-python#122 |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#117 |
| cloudgood | non-compliant | shakenfist/cloudgood#2 |
| development | compliant | - |
| divergulent | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | non-compliant | shakenfist/kerbside-client#2 |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | non-compliant | shakenfist/occystrap#112 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#13 |
| visual-digest-rust | non-compliant | shakenfist/visual-digest-rust#13 |

Details for non-compliant projects:

- **agent-python** (Status): renovate.json does not enable the pre-commit manager, so the hook revisions in .pre-commit-config.yaml are unmanaged and drift silently
- **clingwrap** (Status): renovate.json does not enable the pre-commit manager, so the hook revisions in .pre-commit-config.yaml are unmanaged and drift silently
- **cloudgood** (Status): Missing: .github/workflows/renovate.yml, renovate.json
- **kerbside-client** (Status): Missing: .github/workflows/renovate.yml, renovate.json
- **occystrap** (Status): renovate.json does not enable the pre-commit manager, so the hook revisions in .pre-commit-config.yaml are unmanaged and drift silently
- **uncalibrated-sextant** (Status): Missing: .github/workflows/renovate.yml, renovate.json
- **visual-digest-rust** (Status): Missing: .github/workflows/renovate.yml, renovate.json

## review-coverage

Criterion: [review-coverage.md](/components/development/audits/review-coverage/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | non-compliant | shakenfist/actions#29 |
| agent-python | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | non-compliant | shakenfist/development#45 |
| divergulent | N/A | - |
| instar | N/A | - |
| kerbside | non-compliant | shakenfist/kerbside#227 |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | non-compliant | shakenfist/ryll#304 |
| sfui | N/A | - |
| shakenfist | N/A | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **actions** (Status): 0 of 93 in-scope files reviewed at HEAD; 93 need review (threshold 5)
- **development** (Status): 153 of 174 in-scope files reviewed at HEAD; 21 need review (threshold 5)
- **kerbside** (Status): 120 of 229 in-scope files reviewed at HEAD; 109 need review (threshold 5)
- **ryll** (Status): 85 of 188 in-scope files reviewed at HEAD; 103 need review (threshold 5)

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
| instar | N/A | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | non-compliant | shakenfist/ryll#340 |
| sfui | N/A | - |
| shakenfist | N/A | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **ryll** (Status): 36 tracked file(s) are out of review scope only because no include pattern in .vscode/review-scope.toml names them

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
| visual-digest-rust | non-compliant | shakenfist/visual-digest-rust#14 |

Details for non-compliant projects:

- **uncalibrated-sextant** (Status): clippy unwrap_used lint not set to warn or deny in Cargo.toml; clippy.toml missing allow-unwrap-in-tests = true
- **visual-digest-rust** (Status): clippy unwrap_used lint not set to warn or deny in Cargo.toml; clippy.toml missing allow-unwrap-in-tests = true; digest-decode/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself; shakenfist-visual-digest/Cargo.toml neither inherits workspace lints ([lints] workspace = true) nor defines unwrap_used itself

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
| agent-python | non-compliant | shakenfist/agent-python#113 |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#111 |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | non-compliant | shakenfist/occystrap#101 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#18 |
| visual-digest-rust | non-compliant | shakenfist/visual-digest-rust#18 |

Details for non-compliant projects:

- **agent-python** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
- **clingwrap** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
- **occystrap** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
- **uncalibrated-sextant** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
- **visual-digest-rust** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow

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
| instar | N/A | - |
| kerbside | N/A | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | non-compliant | shakenfist/ryll#322 |
| sfui | N/A | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **ryll** (Status): 1 of 1 HTTP request handler class(es) do not sanitize header values: tools/browser-offer-probe.py:68 (Handler): does not inherit SafeHeaderMixin, so send_header() passes CR and LF straight through

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
| instar | N/A | - |
| kerbside | non-compliant | shakenfist/kerbside#404 |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | non-compliant | shakenfist/private-ci#23 |
| ryll | non-compliant | shakenfist/ryll#349 |
| sfui | N/A | - |
| shakenfist | N/A | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **kerbside** (Status): kerbside/api/static/sfui: 2 commit(s) behind canonical; re-run tools/vendor.sh from an up to date sfui checkout
- **private-ci** (Status): conductor/static/sfui: 2 commit(s) behind canonical; re-run tools/vendor.sh from an up to date sfui checkout
- **ryll** (Status): ryll/src/web/assets/sfui: 4 commit(s) behind canonical; re-run tools/vendor.sh from an up to date sfui checkout

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
| instar | N/A | - |
| kerbside | non-compliant | shakenfist/kerbside#400 |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | non-compliant | shakenfist/shakenfist#4044 |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **kerbside** (Status): Imported but declared only as a transitive pin: packaging (pyproject.toml:110), PyJWT (pyproject.toml:113), requests (pyproject.toml:118), urllib3 (pyproject.toml:125). Declare each above the # START_OF_INDIRECT_DEPS marker; the reconciler drops the generated copy on its next run
- **shakenfist** (Status): Imported but declared only as a transitive pin: jsonschema (pyproject.toml:134), packaging (pyproject.toml:142), pylogrus (pyproject.toml:144), six (pyproject.toml:151). Declare each above the # START_OF_INDIRECT_DEPS marker; the reconciler drops the generated copy on its next run

## unused-declared-dependency

Criterion: [unused-declared-dependency.md](/components/development/audits/unused-declared-dependency/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | non-compliant | shakenfist/agent-python#131 |
| client-python | non-compliant | shakenfist/client-python#383 |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#50 |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | compliant | - |
| instar | N/A | - |
| kerbside | non-compliant | shakenfist/kerbside#399 |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | non-compliant | shakenfist/shakenfist#4043 |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **agent-python** (Status): Declared but never imported: grpcio-status (pyproject.toml:29), grpcio-tools (pyproject.toml:30). Remove each, or record why it is installed with a "# not-imported: <name> -- <reason>" comment in the dependencies array
- **client-python** (Status): Declared but never imported: chardet (pyproject.toml:23), pyyaml (pyproject.toml:27), requests_toolbelt (pyproject.toml:22). Remove each, or record why it is installed with a "# not-imported: <name> -- <reason>" comment in the dependencies array
- **client-python-k3s** (Status): Declared but never imported: prettytable (pyproject.toml:33). Remove each, or record why it is installed with a "# not-imported: <name> -- <reason>" comment in the dependencies array
- **kerbside** (Status): Declared but never imported: bcrypt (pyproject.toml:45), flasgger (pyproject.toml:43), gunicorn (pyproject.toml:46), kerbside-proxy (pyproject.toml:34), mysqlclient (pyproject.toml:66), prometheus-client (pyproject.toml:40), psutil (pyproject.toml:49), pylogrus (pyproject.toml:39), PyMySQL (pyproject.toml:50), typing-extensions (pyproject.toml:61). Remove each, or record why it is installed with a "# not-imported: <name> -- <reason>" comment in the dependencies array
- **shakenfist** (Status): Declared but never imported: chardet (pyproject.toml:101), clingwrap (pyproject.toml:37), gevent (pyproject.toml:67), greenlet (pyproject.toml:66), grpcio-status (pyproject.toml:95), grpcio-tools (pyproject.toml:96), pbr (pyproject.toml:57), requests-toolbelt (pyproject.toml:100), urllib3 (pyproject.toml:102), uv (pyproject.toml:32). Remove each, or record why it is installed with a "# not-imported: <name> -- <reason>" comment in the dependencies array

## version-file-gitignore

Criterion: [version-file-gitignore.md](/components/development/audits/version-file-gitignore/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | non-compliant | shakenfist/agent-python#103 |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#106 |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | compliant | - |
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

- **agent-python** (Status): shakenfist_agent/_version.py is not covered by .gitignore
- **clingwrap** (Status): clingwrap/_version.py is not covered by .gitignore

## workflow-standards

Criterion: [workflow-standards.md](/components/development/audits/workflow-standards/)

| Project | flake8wrap | Runners | Static tags | VM size | Permissions | Linting | devpi fallback | devpi IP | Review marks | Issue |
|---------|--------|--------|--------|--------|--------|--------|--------|--------|--------|--------|
| actions | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | compliant | - |
| agent-python | non-compliant | non-compliant | compliant | non-compliant | compliant | compliant | N/A | compliant | N/A | shakenfist/agent-python#105, shakenfist/agent-python#130, shakenfist/agent-python#82 |
| client-python | compliant | compliant | compliant | non-compliant | compliant | compliant | N/A | compliant | N/A | shakenfist/client-python#378 |
| client-python-k3s | compliant | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | - |
| clingwrap | compliant | compliant | compliant | non-compliant | compliant | compliant | N/A | compliant | N/A | shakenfist/clingwrap#125 |
| cloudgood | N/A | N/A | N/A | N/A | N/A | compliant | N/A | N/A | N/A | - |
| development | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | - |
| divergulent | compliant | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | - |
| instar | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | - |
| kerbside | compliant | compliant | compliant | compliant | compliant | compliant | compliant | compliant | compliant | - |
| kerbside-client | non-compliant | N/A | N/A | N/A | N/A | non-compliant | N/A | N/A | N/A | shakenfist/kerbside-client#4, shakenfist/kerbside-client#6 |
| kerbside-patches | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | - |
| library-utilities | compliant | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | - |
| occystrap | non-compliant | compliant | compliant | non-compliant | compliant | compliant | N/A | compliant | N/A | shakenfist/occystrap#126, shakenfist/occystrap#67 |
| private-ci | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | - |
| ryll | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | - |
| sfui | N/A | compliant | compliant | compliant | compliant | compliant | compliant | compliant | N/A | - |
| shakenfist | non-compliant | compliant | compliant | non-compliant | compliant | compliant | non-compliant | compliant | N/A | shakenfist/shakenfist#3057, shakenfist/shakenfist#3418, shakenfist/shakenfist#3977 |
| uncalibrated-sextant | N/A | non-compliant | compliant | compliant | non-compliant | compliant | N/A | compliant | N/A | shakenfist/uncalibrated-sextant#15, shakenfist/uncalibrated-sextant#17 |
| visual-digest-rust | N/A | compliant | compliant | non-compliant | compliant | compliant | N/A | compliant | N/A | shakenfist/visual-digest-rust#15 |

Details for non-compliant projects:

- **agent-python** (flake8wrap): Missing shellcheck disable=SC2086 directive
- **agent-python** (Runners): 2 unmarked GitHub-hosted runner reference(s): functional-tests.yml:103 (ubuntu-latest), functional-tests.yml:114 (ubuntu-latest). Move to a self-hosted runner, or mark deliberate exceptions with an "audit-ok: github-hosted-runner" comment
- **agent-python** (VM size): 1 "vm" runner job(s) naming no size: functional-tests.yml:25 (self-hosted, vm, debian-12). The conductor takes the runner size from the labels and falls back to the first CI_SIZES entry -- "xs", one vCPU and 2048 MB -- when it finds none, so an omitted size is a silent downgrade to the smallest runner rather than a free choice. Add the size the job actually wants (xs/s/m/l/xl, or m-bigdisk/xl-bigdisk when the job needs the disk); "xs" is a valid answer stated explicitly. A job which genuinely cannot name one marks the line "audit-ok: vm-runner-size" with the reason
- **client-python** (VM size): 3 "vm" runner job(s) naming no size: code-formatting.yml:19 (self-hosted, vm), functional-tests.yml:23 (self-hosted, vm), supply-chain.yml:81 (self-hosted, vm). The conductor takes the runner size from the labels and falls back to the first CI_SIZES entry -- "xs", one vCPU and 2048 MB -- when it finds none, so an omitted size is a silent downgrade to the smallest runner rather than a free choice. Add the size the job actually wants (xs/s/m/l/xl, or m-bigdisk/xl-bigdisk when the job needs the disk); "xs" is a valid answer stated explicitly. A job which genuinely cannot name one marks the line "audit-ok: vm-runner-size" with the reason
- **clingwrap** (VM size): 1 "vm" runner job(s) naming no size: functional-tests.yml:22 (self-hosted, vm, debian-12). The conductor takes the runner size from the labels and falls back to the first CI_SIZES entry -- "xs", one vCPU and 2048 MB -- when it finds none, so an omitted size is a silent downgrade to the smallest runner rather than a free choice. Add the size the job actually wants (xs/s/m/l/xl, or m-bigdisk/xl-bigdisk when the job needs the disk); "xs" is a valid answer stated explicitly. A job which genuinely cannot name one marks the line "audit-ok: vm-runner-size" with the reason
- **kerbside-client** (flake8wrap): Missing shellcheck disable=SC2086 directive
- **kerbside-client** (Linting): Missing .pre-commit-config.yaml
- **occystrap** (flake8wrap): Missing shellcheck disable=SC2086 directive
- **occystrap** (VM size): 2 "vm" runner job(s) naming no size: functional-tests.yml:17 (self-hosted, vm, debian-12), python-unit-tests.yml:16 (self-hosted, vm, debian-12). The conductor takes the runner size from the labels and falls back to the first CI_SIZES entry -- "xs", one vCPU and 2048 MB -- when it finds none, so an omitted size is a silent downgrade to the smallest runner rather than a free choice. Add the size the job actually wants (xs/s/m/l/xl, or m-bigdisk/xl-bigdisk when the job needs the disk); "xs" is a valid answer stated explicitly. A job which genuinely cannot name one marks the line "audit-ok: vm-runner-size" with the reason
- **shakenfist** (flake8wrap): Missing shellcheck disable=SC2086 directive
- **shakenfist** (VM size): 2 "vm" runner job(s) naming no size: functional-tests.yml:718 (self-hosted, vm, debian-12), pin-indirect-dependencies.yml:50 (self-hosted, vm, debian-12). The conductor takes the runner size from the labels and falls back to the first CI_SIZES entry -- "xs", one vCPU and 2048 MB -- when it finds none, so an omitted size is a silent downgrade to the smallest runner rather than a free choice. Add the size the job actually wants (xs/s/m/l/xl, or m-bigdisk/xl-bigdisk when the job needs the disk); "xs" is a valid answer stated explicitly. A job which genuinely cannot name one marks the line "audit-ok: vm-runner-size" with the reason
- **shakenfist** (devpi fallback): 9 devpi-backed env block(s) missing a PIP_EXTRA_INDEX_URL pypi fallback: code-formatting.yml:27, codeql-analysis.yml:20, docs-tests.yml:19, functional-tests.yml:26, issue-fix.yml:133, publish-website.yml:17, release.yml:26, scheduled-tests.yml:24, test-drift-fix.yml:78. Add "PIP_EXTRA_INDEX_URL: https://pypi.org/simple/" alongside PIP_INDEX_URL so a devpi cold-cache miss (empty index for a first-touch package) falls back to pypi instead of failing with "from versions: none"
- **uncalibrated-sextant** (Runners): 1 unmarked GitHub-hosted runner reference(s): pre-commit.yml:10 (ubuntu-latest). Move to a self-hosted runner, or mark deliberate exceptions with an "audit-ok: github-hosted-runner" comment
- **uncalibrated-sextant** (Permissions): 1 workflow(s) missing top-level permissions: pre-commit.yml
- **visual-digest-rust** (VM size): 1 "vm" runner job(s) naming no size: ci.yml:16 (self-hosted, vm, debian-12). The conductor takes the runner size from the labels and falls back to the first CI_SIZES entry -- "xs", one vCPU and 2048 MB -- when it finds none, so an omitted size is a silent downgrade to the smallest runner rather than a free choice. Add the size the job actually wants (xs/s/m/l/xl, or m-bigdisk/xl-bigdisk when the job needs the disk); "xs" is a valid answer stated explicitly. A job which genuinely cannot name one marks the line "audit-ok: vm-runner-size" with the reason

## Criteria with no automated check

These criteria are written down and judged by a person, so they have no table above. Each says why in its own page:

- [test-coverage.md](/components/development/audits/test-coverage/)
<!-- consistency-audit:end -->
