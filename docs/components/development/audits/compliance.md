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
*Generated 2026-08-26T06:56:26.297909+00:00 from `scripts/audit-check.py`; do not edit.*

## ci-review-automation

Criterion: [ci-review-automation.md](/components/development/audits/ci-review-automation/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | non-compliant | shakenfist/agent-python#126 |
| client-python | non-compliant | shakenfist/client-python#367 |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#36 |
| clingwrap | non-compliant | shakenfist/clingwrap#121 |
| cloudgood | non-compliant | shakenfist/cloudgood#1 |
| development | compliant | - |
| divergulent | compliant | - |
| instar | non-compliant | shakenfist/instar#515 |
| kerbside | non-compliant | shakenfist/kerbside#360 |
| kerbside-patches | compliant | - |
| library-utilities | non-compliant | shakenfist/library-utilities#32 |
| occystrap | non-compliant | shakenfist/occystrap#120 |
| private-ci | N/A | - |
| ryll | non-compliant | shakenfist/ryll#303 |
| sfui | non-compliant | shakenfist/sfui#26 |
| shakenfist | non-compliant | shakenfist/shakenfist#3314 |

Details for non-compliant projects:

- **agent-python** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml); it is unused, and its workflow holds contents: write on the pull request branch
- **client-python** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml); it is unused, and its workflow holds contents: write on the pull request branch
- **client-python-k3s** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh); it is unused, and its workflow holds contents: write on the pull request branch
- **clingwrap** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py, tools/review-schema.json); it is unused, and its workflow holds contents: write on the pull request branch
- **cloudgood** (Status): Missing workflows: pr-re-review.yml
- **instar** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py, tools/review-schema.json); it is unused, and its workflow holds contents: write on the pull request branch
- **kerbside** (Status): the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py, tools/review-schema.json); it is unused, and its workflow holds contents: write on the pull request branch
- **library-utilities** (Status): Missing pr-re-review.yml; Missing pr-retest.yml; No workflow uses shared action review-pr-with-claude@main
- **occystrap** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py, tools/review-schema.json); it is unused, and its workflow holds contents: write on the pull request branch
- **ryll** (Status): the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py, tools/review-schema.json); it is unused, and its workflow holds contents: write on the pull request branch
- **sfui** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py); it is unused, and its workflow holds contents: write on the pull request branch
- **shakenfist** (Status): Missing pr-retest.yml; pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py, tools/review-schema.json); it is unused, and its workflow holds contents: write on the pull request branch

## console-logging

Criterion: [console-logging.md](/components/development/audits/console-logging/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | non-compliant | shakenfist/agent-python#128 |
| client-python | non-compliant | shakenfist/client-python#371 |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| instar | N/A | - |
| kerbside | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | non-compliant | shakenfist/occystrap#124 |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | non-compliant | shakenfist/shakenfist#3909 |

Details for non-compliant projects:

- **agent-python** (Status): 1 of 1 console entry point(s) calling setup_console() do not configure the root logger -- shakenfist_agent/main.py: missing logging.basicConfig() (INFO from every other module reaches a root logger with no handler and is dropped); propagate = False on its own logger (its own lines are emitted twice once root has a handler)
- **client-python** (Status): 1 of 1 console entry point(s) calling setup_console() do not configure the root logger -- shakenfist_client/main.py: missing logging.basicConfig() (INFO from every other module reaches a root logger with no handler and is dropped); propagate = False on its own logger (its own lines are emitted twice once root has a handler)
- **occystrap** (Status): 1 of 1 console entry point(s) calling setup_console() do not configure the root logger -- occystrap/main.py: missing logging.basicConfig() (INFO from every other module reaches a root logger with no handler and is dropped); propagate = False on its own logger (its own lines are emitted twice once root has a handler)
- **shakenfist** (Status): 2 of 2 console entry point(s) calling setup_console() do not configure the root logger -- shakenfist/client/backup.py: missing logging.basicConfig() (INFO from every other module reaches a root logger with no handler and is dropped); propagate = False on its own logger (its own lines are emitted twice once root has a handler); shakenfist/client/ctl.py: missing logging.basicConfig() (INFO from every other module reaches a root logger with no handler and is dropped); propagate = False on its own logger (its own lines are emitted twice once root has a handler)

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
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |

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
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |

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
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | compliant | - |

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
| instar | non-compliant | shakenfist/instar#502 |
| kerbside | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | non-compliant | shakenfist/shakenfist#3792 |

Details for non-compliant projects:

- **cloudgood** (Status): 2 relative link(s) in docs/ that do not resolve to a file inside docs/ (use absolute https://github.com/... URLs, which survive the docs site import): docs/index.md -> more-fundamentals.md, docs/virtualization-history.md -> more-fundamentals.md
- **instar** (Status): 46 relative link(s) in docs/ that do not resolve to a file inside docs/ (use absolute https://github.com/... URLs, which survive the docs site import): docs/amend.md -> ../src/crates/amend/src/qcow2.rs, docs/amend.md -> ../tests/test_amend.py, docs/bench.md -> ../src/crates/bench/, docs/bench.md -> ../src/crates/qcow2-write-exec/, docs/bench.md -> ../src/crates/qcow2-write/, docs/bench.md -> ../src/operations/bench/, docs/bench.md -> ../tests/test_bench.py, docs/bitmap.md -> ../src/crates/bitmap/, docs/bitmap.md -> ../src/operations/bitmap/, docs/bitmap.md -> ../tests/test_bitmap.py (+36 more)
- **shakenfist** (Status): 13 relative link(s) in docs/ that do not resolve to a file inside docs/ (use absolute https://github.com/... URLs, which survive the docs site import): docs/operator_guide/database.md -> ../../shakenfist/schema/object_filter.py, docs/plans/PLAN-sql-pushdown-filtering-phase-03-instance-network.md -> shakenfist/instance.py#L177-L183, docs/plans/PLAN-sql-pushdown-filtering-phase-03-instance-network.md -> shakenfist/instance.py#L344, docs/plans/PLAN-sql-pushdown-filtering-phase-03-instance-network.md -> shakenfist/network/network.py#L182, docs/plans/PLAN-sql-pushdown-filtering-phase-03-instance-network.md -> shakenfist/network/network.py#L964, docs/plans/PLAN-sql-pushdown-filtering-phase-04-iterators.md -> shakenfist/artifact.py#L609, docs/plans/PLAN-sql-pushdown-filtering-phase-04-iterators.md -> shakenfist/external_api/artifact.py#L387, docs/plans/PLAN-sql-pushdown-filtering-phase-04-iterators.md -> shakenfist/external_api/instance.py#L893, docs/plans/PLAN-sql-pushdown-filtering-phase-04-iterators.md -> shakenfist/external_api/network.py#L292, docs/plans/PLAN-sql-pushdown-filtering-phase-04-iterators.md -> shakenfist/instance.py#L2137 (+3 more)

## expensive-lane-path-filter

Criterion: [expensive-lane-path-filter.md](/components/development/audits/expensive-lane-path-filter/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | non-compliant | shakenfist/agent-python#123 |
| client-python | compliant | - |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#29 |
| clingwrap | non-compliant | shakenfist/clingwrap#118 |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | non-compliant | shakenfist/occystrap#113 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | non-compliant | shakenfist/sfui#14 |
| shakenfist | compliant | - |

Details for non-compliant projects:

- **agent-python** (Status): 1 expensive lane(s) triggered by pull_request or merge_group without adequate path filtering: functional-tests.yml (no path filtering). Add a check_paths filter job (see kerbside functional-tests.yml) or, only for workflows backing no required status check, trigger-level paths-ignore, excluding docs/** and the review-tracking files; mark deliberate exceptions with an "audit-ok: no-path-filter" comment
- **client-python-k3s** (Status): 1 expensive lane(s) triggered by pull_request or merge_group without adequate path filtering: functional-tests.yml (no path filtering). Add a check_paths filter job (see kerbside functional-tests.yml) or, only for workflows backing no required status check, trigger-level paths-ignore, excluding docs/** and the review-tracking files; mark deliberate exceptions with an "audit-ok: no-path-filter" comment
- **clingwrap** (Status): 1 expensive lane(s) triggered by pull_request or merge_group without adequate path filtering: functional-tests.yml (no path filtering). Add a check_paths filter job (see kerbside functional-tests.yml) or, only for workflows backing no required status check, trigger-level paths-ignore, excluding docs/** and the review-tracking files; mark deliberate exceptions with an "audit-ok: no-path-filter" comment
- **occystrap** (Status): 2 expensive lane(s) triggered by pull_request or merge_group without adequate path filtering: functional-tests.yml (no path filtering), python-unit-tests.yml (no path filtering). Add a check_paths filter job (see kerbside functional-tests.yml) or, only for workflows backing no required status check, trigger-level paths-ignore, excluding docs/** and the review-tracking files; mark deliberate exceptions with an "audit-ok: no-path-filter" comment
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
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | non-compliant | shakenfist/library-utilities#35 |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |

Details for non-compliant projects:

- **cloudgood** (Status): Missing .github/workflows/export-repo-config.yml
- **library-utilities** (Status): Missing .github/workflows/export-repo-config.yml

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
| kerbside-patches | compliant | - |
| library-utilities | non-compliant | shakenfist/library-utilities#36 |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | non-compliant | shakenfist/shakenfist#3056 |

Details for non-compliant projects:

- **agent-python** (Status): Secret scanning not enabled; Secret scanning push protection not enabled
- **cloudgood** (Status): Secret scanning not enabled; Secret scanning push protection not enabled
- **library-utilities** (Status): Missing .github/workflows/codeql-analysis.yml; Secret scanning not enabled; Secret scanning push protection not enabled
- **shakenfist** (Status): Secret scanning not enabled; Secret scanning push protection not enabled

## llm-context-lint-ci

Criterion: [llm-context-lint-ci.md](/components/development/audits/llm-context-lint-ci/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | non-compliant | shakenfist/agent-python#125 |
| client-python | non-compliant | shakenfist/client-python#366 |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#35 |
| clingwrap | non-compliant | shakenfist/clingwrap#120 |
| cloudgood | non-compliant | shakenfist/cloudgood#8 |
| development | compliant | - |
| divergulent | compliant | - |
| instar | non-compliant | shakenfist/instar#514 |
| kerbside | non-compliant | shakenfist/kerbside#359 |
| kerbside-patches | compliant | - |
| library-utilities | N/A | - |
| occystrap | non-compliant | shakenfist/occystrap#119 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | non-compliant | shakenfist/sfui#25 |
| shakenfist | non-compliant | shakenfist/shakenfist#3832 |

Details for non-compliant projects:

- **agent-python** (Status): skillsaw does not run from .pre-commit-config.yaml or a CI workflow
- **client-python** (Status): skillsaw does not run from .pre-commit-config.yaml or a CI workflow
- **client-python-k3s** (Status): skillsaw does not run from .pre-commit-config.yaml or a CI workflow
- **clingwrap** (Status): skillsaw does not run from .pre-commit-config.yaml or a CI workflow
- **cloudgood** (Status): skillsaw does not run from .pre-commit-config.yaml or a CI workflow
- **instar** (Status): skillsaw does not run from .pre-commit-config.yaml or a CI workflow
- **kerbside** (Status): skillsaw does not run from a CI workflow
- **occystrap** (Status): skillsaw does not run from .pre-commit-config.yaml or a CI workflow
- **sfui** (Status): skillsaw does not run from .pre-commit-config.yaml or a CI workflow
- **shakenfist** (Status): skillsaw does not run from .pre-commit-config.yaml or a CI workflow

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
| instar | non-compliant | shakenfist/instar#513 |
| kerbside | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | N/A | - |
| occystrap | non-compliant | shakenfist/occystrap#118 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | non-compliant | shakenfist/shakenfist#3831 |

Details for non-compliant projects:

- **instar** (Status): Markdown that will never load as a skill: .claude/skills/build-and-test.md, .claude/skills/correct-fixes.md, .claude/skills/documentation-updates.md, .claude/skills/error-handling.md, .claude/skills/instar-add-test-image.md, .claude/skills/instar-calltable.md, .claude/skills/instar-debug.md, .claude/skills/instar-format.md, .claude/skills/instar-new-op.md, .claude/skills/pr-preparation.md, .claude/skills/testing-discipline.md, .claude/skills/verbose-print.md
- **occystrap** (Status): Markdown that will never load as a skill: .claude/skills/documentation-updates.md, .claude/skills/pr-preparation.md, .claude/skills/testing-discipline.md
- **shakenfist** (Status): Markdown that will never load as a skill: .claude/skills/add-grpc-service.md, .claude/skills/add-mypy-coverage.md

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
| kerbside-patches | compliant | - |
| library-utilities | N/A | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |

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
| kerbside-patches | compliant | - |
| library-utilities | non-compliant | shakenfist/library-utilities#30 |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |

Details for non-compliant projects:

- **library-utilities** (Status): Missing: AGENTS.md, ARCHITECTURE.md

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
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | N/A | - |
| shakenfist | compliant | - |

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
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | N/A | - |
| shakenfist | compliant | - |

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
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | compliant | - |

## plan-index

Criterion: [plan-index.md](/components/development/audits/plan-index/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| client-python | non-compliant | shakenfist/client-python#365 |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#32 |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| instar | non-compliant | shakenfist/instar#506 |
| kerbside | compliant | - |
| kerbside-patches | N/A | - |
| library-utilities | non-compliant | shakenfist/library-utilities#42 |
| occystrap | non-compliant | shakenfist/occystrap#116 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | non-compliant | shakenfist/sfui#24 |
| shakenfist | compliant | - |

Details for non-compliant projects:

- **client-python** (Status): docs/plans/index.md is missing, so none of the 1 plan(s) in docs/plans/ are registered
- **client-python-k3s** (Status): docs/plans/index.md is missing, so none of the 2 plan(s) in docs/plans/ are registered
- **instar** (Status): 1 status cell(s) outside the shared vocabulary (Proposed, Not started, In progress, Blocked, Complete, Abandoned, Superseded): instar amend subcommand ("1.1 (qcow2 v2⇔v3 version transition, ...")
- **library-utilities** (Status): docs/plans/index.md is missing, so none of the 1 plan(s) in docs/plans/ are registered
- **occystrap** (Status): index has no plan table (it must list plans in a table led by Date and Plan columns, not as prose or a bullet list); 4 master plan(s) not listed in the index: PLAN-make-the-speed.md, PLAN-post-write-verification.md, PLAN-registry-proxy.md, PLAN-structured-logging.md
- **sfui** (Status): docs/plans/index.md is missing, so none of the 3 plan(s) in docs/plans/ are registered

## plan-phase-references

Criterion: [plan-phase-references.md](/components/development/audits/plan-phase-references/)

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
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | non-compliant | shakenfist/shakenfist#3732 |

Details for non-compliant projects:

- **shakenfist** (Status): 17 plan phase reference(s) in documentation (describe the current behaviour, or link the master plan in docs/plans/ instead of citing a phase number): ARCHITECTURE.md:199, docs/developer_guide/database_internals.md:315, docs/developer_guide/database_internals.md:319, docs/developer_guide/subsystem_internals.md:51, docs/developer_guide/subsystem_internals.md:151, docs/developer_guide/subsystem_internals.md:153, docs/developer_guide/subsystem_internals.md:172, docs/developer_guide/subsystem_internals.md:226, docs/developer_guide/subsystem_internals.md:243, docs/developer_guide/subsystem_internals.md:447 (+7 more)

## plan-source-references

Criterion: [plan-source-references.md](/components/development/audits/plan-source-references/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | compliant | - |
| instar | non-compliant | shakenfist/instar#516 |
| kerbside | compliant | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | N/A | - |
| shakenfist | compliant | - |

Details for non-compliant projects:

- **instar** (Status): 2 of 197 plan reference(s) in source or configuration do not resolve (update the path, or use an absolute https://github.com/... URL for a plan in another repository): src/crates/qcow2-write-exec/src/growth.rs:13 -> docs/plans/PLAN-qcow2-write-infrastructure-phase-07-write.md, tests/test_adversarial.py:8 -> PLAN-adversarial-images.md

## plan-template

Criterion: [plan-template.md](/components/development/audits/plan-template/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| client-python | N/A | - |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#33 |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | non-compliant | shakenfist/divergulent#79 |
| instar | non-compliant | shakenfist/instar#523 |
| kerbside | non-compliant | shakenfist/kerbside#368 |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | non-compliant | shakenfist/occystrap#117 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | N/A | - |
| shakenfist | non-compliant | shakenfist/shakenfist#3892 |

Details for non-compliant projects:

- **client-python-k3s** (Status): missing shared block plan-status-vocabulary (copy it verbatim from templates/shared-blocks/plan-status-vocabulary.md in the development repository); missing shared block plan-push-audit-phase (copy it verbatim from templates/shared-blocks/plan-push-audit-phase.md in the development repository)
- **divergulent** (Status): missing shared block plan-push-audit-phase (copy it verbatim from templates/shared-blocks/plan-push-audit-phase.md in the development repository)
- **instar** (Status): missing shared block plan-push-audit-phase (copy it verbatim from templates/shared-blocks/plan-push-audit-phase.md in the development repository)
- **kerbside** (Status): missing shared block plan-push-audit-phase (copy it verbatim from templates/shared-blocks/plan-push-audit-phase.md in the development repository)
- **occystrap** (Status): missing shared block plan-status-vocabulary (copy it verbatim from templates/shared-blocks/plan-status-vocabulary.md in the development repository); missing shared block plan-push-audit-phase (copy it verbatim from templates/shared-blocks/plan-push-audit-phase.md in the development repository)
- **shakenfist** (Status): missing shared block plan-push-audit-phase (copy it verbatim from templates/shared-blocks/plan-push-audit-phase.md in the development repository)

## push-audit

Criterion: [push-audit.md](/components/development/audits/push-audit/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| client-python | N/A | - |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#26 |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | non-compliant | shakenfist/divergulent#82 |
| instar | non-compliant | shakenfist/instar#491 |
| kerbside | non-compliant | shakenfist/kerbside#370 |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | non-compliant | shakenfist/occystrap#110 |
| private-ci | N/A | - |
| ryll | non-compliant | shakenfist/ryll#323 |
| sfui | non-compliant | shakenfist/sfui#15 |
| shakenfist | non-compliant | shakenfist/shakenfist#3911 |

Details for non-compliant projects:

- **client-python-k3s** (Status): missing shared block llm-doc-discipline (copy it verbatim from templates/shared-blocks/llm-doc-discipline.md in the development repository); missing shared block plan-phase-references (copy it verbatim from templates/shared-blocks/plan-phase-references.md in the development repository); missing shared block path-traversal-review (copy it verbatim from templates/shared-blocks/path-traversal-review.md in the development repository); missing shared block python-version-discipline (copy it verbatim from templates/shared-blocks/python-version-discipline.md in the development repository); missing shared block functional-test-coverage (copy it verbatim from templates/shared-blocks/functional-test-coverage.md in the development repository)
- **divergulent** (Status): missing shared block path-traversal-review (copy it verbatim from templates/shared-blocks/path-traversal-review.md in the development repository); missing shared block python-version-discipline (copy it verbatim from templates/shared-blocks/python-version-discipline.md in the development repository); missing shared block functional-test-coverage (copy it verbatim from templates/shared-blocks/functional-test-coverage.md in the development repository)
- **instar** (Status): missing shared block llm-doc-discipline (copy it verbatim from templates/shared-blocks/llm-doc-discipline.md in the development repository); missing shared block plan-phase-references (copy it verbatim from templates/shared-blocks/plan-phase-references.md in the development repository); missing shared block path-traversal-review (copy it verbatim from templates/shared-blocks/path-traversal-review.md in the development repository); missing shared block python-version-discipline (copy it verbatim from templates/shared-blocks/python-version-discipline.md in the development repository); missing shared block functional-test-coverage (copy it verbatim from templates/shared-blocks/functional-test-coverage.md in the development repository)
- **kerbside** (Status): missing shared block path-traversal-review (copy it verbatim from templates/shared-blocks/path-traversal-review.md in the development repository); missing shared block python-version-discipline (copy it verbatim from templates/shared-blocks/python-version-discipline.md in the development repository); missing shared block functional-test-coverage (copy it verbatim from templates/shared-blocks/functional-test-coverage.md in the development repository)
- **occystrap** (Status): missing shared block llm-doc-discipline (copy it verbatim from templates/shared-blocks/llm-doc-discipline.md in the development repository); missing shared block plan-phase-references (copy it verbatim from templates/shared-blocks/plan-phase-references.md in the development repository); missing shared block path-traversal-review (copy it verbatim from templates/shared-blocks/path-traversal-review.md in the development repository); missing shared block python-version-discipline (copy it verbatim from templates/shared-blocks/python-version-discipline.md in the development repository); missing shared block functional-test-coverage (copy it verbatim from templates/shared-blocks/functional-test-coverage.md in the development repository); AGENTS.md does not reference PUSH-AUDIT.md (an audit nothing points at does not get run)
- **ryll** (Status): missing shared block path-traversal-review (copy it verbatim from templates/shared-blocks/path-traversal-review.md in the development repository); missing shared block python-version-discipline (copy it verbatim from templates/shared-blocks/python-version-discipline.md in the development repository); missing shared block functional-test-coverage (copy it verbatim from templates/shared-blocks/functional-test-coverage.md in the development repository)
- **sfui** (Status): missing shared block llm-doc-discipline (copy it verbatim from templates/shared-blocks/llm-doc-discipline.md in the development repository); missing shared block plan-phase-references (copy it verbatim from templates/shared-blocks/plan-phase-references.md in the development repository); missing shared block path-traversal-review (copy it verbatim from templates/shared-blocks/path-traversal-review.md in the development repository); missing shared block python-version-discipline (copy it verbatim from templates/shared-blocks/python-version-discipline.md in the development repository); missing shared block functional-test-coverage (copy it verbatim from templates/shared-blocks/functional-test-coverage.md in the development repository); AGENTS.md does not reference PUSH-AUDIT.md (an audit nothing points at does not get run)
- **shakenfist** (Status): missing shared block path-traversal-review (copy it verbatim from templates/shared-blocks/path-traversal-review.md in the development repository); missing shared block python-version-discipline (copy it verbatim from templates/shared-blocks/python-version-discipline.md in the development repository); missing shared block functional-test-coverage (copy it verbatim from templates/shared-blocks/functional-test-coverage.md in the development repository)

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
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | compliant | - |

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
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | compliant | - |

## readme-absolute-links

Criterion: [readme-absolute-links.md](/components/development/audits/readme-absolute-links/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | non-compliant | shakenfist/agent-python#107 |
| client-python | non-compliant | shakenfist/client-python#345 |
| client-python-k3s | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#108 |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | non-compliant | shakenfist/library-utilities#40 |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |

Details for non-compliant projects:

- **agent-python** (Status): 5 relative link target(s) in README.md (use absolute URLs so the README renders off the repo landing page): AGENTS.md, ARCHITECTURE.md, docs/developer-guide.md, docs/index.md, docs/protocol.md
- **client-python** (Status): 3 relative link target(s) in README.md (use absolute URLs so the README renders off the repo landing page): AGENTS.md, ARCHITECTURE.md, RELEASE-SETUP.md
- **clingwrap** (Status): 5 relative link target(s) in README.md (use absolute URLs so the README renders off the repo landing page): AGENTS.md, ARCHITECTURE.md, RELEASE-SETUP.md, docs/, docs/index.md
- **library-utilities** (Status): 1 relative link target(s) in README.md (use absolute URLs so the README renders off the repo landing page): docs/log-record-fields.md

## readme-structure

Criterion: [readme-structure.md](/components/development/audits/readme-structure/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| client-python | non-compliant | shakenfist/client-python#353 |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#22 |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |

Details for non-compliant projects:

- **client-python** (Status): README.md has no link into docs/ despite a docs/ directory existing; add curated links to the detailed documentation
- **client-python-k3s** (Status): README.md has no link into docs/ despite a docs/ directory existing; add curated links to the detailed documentation

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
| instar | N/A | - |
| kerbside | compliant | - |
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | compliant | - |

## renovate

Criterion: [renovate.md](/components/development/audits/renovate/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | non-compliant | shakenfist/agent-python#122 |
| client-python | non-compliant | shakenfist/client-python#362 |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#28 |
| clingwrap | non-compliant | shakenfist/clingwrap#117 |
| cloudgood | non-compliant | shakenfist/cloudgood#2 |
| development | compliant | - |
| divergulent | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | non-compliant | shakenfist/library-utilities#33 |
| occystrap | non-compliant | shakenfist/occystrap#112 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | non-compliant | shakenfist/shakenfist#3757 |

Details for non-compliant projects:

- **agent-python** (Status): renovate.json does not enable the pre-commit manager, so the hook revisions in .pre-commit-config.yaml are unmanaged and drift silently
- **client-python** (Status): renovate.json does not enable the pre-commit manager, so the hook revisions in .pre-commit-config.yaml are unmanaged and drift silently
- **client-python-k3s** (Status): renovate.json does not enable the pre-commit manager, so the hook revisions in .pre-commit-config.yaml are unmanaged and drift silently
- **clingwrap** (Status): renovate.json does not enable the pre-commit manager, so the hook revisions in .pre-commit-config.yaml are unmanaged and drift silently
- **cloudgood** (Status): Missing: .github/workflows/renovate.yml, renovate.json
- **library-utilities** (Status): Missing: .github/workflows/renovate.yml, renovate.json
- **occystrap** (Status): renovate.json does not enable the pre-commit manager, so the hook revisions in .pre-commit-config.yaml are unmanaged and drift silently
- **shakenfist** (Status): renovate.json does not enable the pre-commit manager, so the hook revisions in .pre-commit-config.yaml are unmanaged and drift silently

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
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | non-compliant | shakenfist/ryll#304 |
| sfui | N/A | - |
| shakenfist | N/A | - |

Details for non-compliant projects:

- **actions** (Status): 0 of 89 in-scope files reviewed at HEAD; 89 need review (threshold 5)
- **development** (Status): 14 of 77 in-scope files reviewed at HEAD; 63 need review (threshold 5)
- **kerbside** (Status): 125 of 194 in-scope files reviewed at HEAD; 69 need review (threshold 5)
- **ryll** (Status): 97 of 178 in-scope files reviewed at HEAD; 81 need review (threshold 5)

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
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | N/A | - |
| shakenfist | N/A | - |

## secret-handling

Criterion: [secret-handling.md](/components/development/audits/secret-handling/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | non-compliant | shakenfist/agent-python#113 |
| client-python | non-compliant | shakenfist/client-python#354 |
| client-python-k3s | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#111 |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | non-compliant | shakenfist/library-utilities#41 |
| occystrap | non-compliant | shakenfist/occystrap#101 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |

Details for non-compliant projects:

- **agent-python** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
- **client-python** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
- **clingwrap** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
- **library-utilities** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
- **occystrap** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow

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
| divergulent | non-compliant | shakenfist/divergulent#81 |
| instar | N/A | - |
| kerbside | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | non-compliant | shakenfist/ryll#322 |
| sfui | N/A | - |
| shakenfist | non-compliant | shakenfist/shakenfist#3910 |

Details for non-compliant projects:

- **divergulent** (Status): 2 of 2 HTTP request handler class(es) do not sanitize header values: divergulent/tests/test_fetch.py:164 (ErrorHandler): does not inherit SafeHeaderMixin, so send_header() passes CR and LF straight through; divergulent/tests/test_fetch.py:37 (Handler): does not inherit SafeHeaderMixin, so send_header() passes CR and LF straight through
- **ryll** (Status): 1 of 1 HTTP request handler class(es) do not sanitize header values: tools/browser-offer-probe.py:68 (Handler): does not inherit SafeHeaderMixin, so send_header() passes CR and LF straight through
- **shakenfist** (Status): 1 of 1 HTTP request handler class(es) do not sanitize header values: shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_federation.py:57 (_JWKSHandler): does not inherit SafeHeaderMixin, so send_header() passes CR and LF straight through

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
| kerbside | compliant | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | compliant | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | N/A | - |

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
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | compliant | - |

Details for non-compliant projects:

- **agent-python** (Status): shakenfist_agent/_version.py is not covered by .gitignore
- **clingwrap** (Status): clingwrap/_version.py is not covered by .gitignore

## workflow-standards

Criterion: [workflow-standards.md](/components/development/audits/workflow-standards/)

| Project | Permissions | Linting | Review marks | flake8wrap | Runners | Static tags | devpi fallback | devpi IP | Issue |
|---------|--------|--------|--------|--------|--------|--------|--------|--------|--------|
| actions | compliant | compliant | compliant | N/A | compliant | compliant | N/A | compliant | - |
| agent-python | compliant | compliant | N/A | non-compliant | non-compliant | compliant | N/A | compliant | shakenfist/agent-python#105, shakenfist/agent-python#82 |
| client-python | compliant | compliant | N/A | compliant | compliant | compliant | N/A | compliant | - |
| client-python-k3s | compliant | compliant | N/A | compliant | compliant | compliant | N/A | compliant | - |
| clingwrap | compliant | compliant | N/A | compliant | compliant | compliant | N/A | compliant | - |
| cloudgood | N/A | compliant | N/A | N/A | N/A | N/A | N/A | N/A | - |
| development | compliant | compliant | N/A | N/A | compliant | compliant | N/A | compliant | - |
| divergulent | compliant | compliant | N/A | compliant | compliant | compliant | N/A | compliant | - |
| instar | compliant | compliant | N/A | N/A | compliant | compliant | N/A | compliant | - |
| kerbside | compliant | compliant | compliant | compliant | compliant | compliant | compliant | compliant | - |
| kerbside-patches | compliant | compliant | N/A | N/A | compliant | compliant | N/A | compliant | - |
| library-utilities | compliant | compliant | N/A | compliant | compliant | compliant | N/A | compliant | - |
| occystrap | compliant | compliant | N/A | non-compliant | compliant | compliant | N/A | compliant | shakenfist/occystrap#67 |
| private-ci | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | - |
| ryll | compliant | compliant | N/A | N/A | compliant | compliant | N/A | compliant | - |
| sfui | compliant | compliant | N/A | N/A | compliant | compliant | compliant | compliant | - |
| shakenfist | compliant | compliant | N/A | non-compliant | compliant | compliant | non-compliant | compliant | shakenfist/shakenfist#3057, shakenfist/shakenfist#3418 |

Details for non-compliant projects:

- **agent-python** (flake8wrap): Missing shellcheck disable=SC2086 directive
- **agent-python** (Runners): 2 unmarked GitHub-hosted runner reference(s): functional-tests.yml:103 (ubuntu-latest), functional-tests.yml:114 (ubuntu-latest). Move to a self-hosted runner, or mark deliberate exceptions with an "audit-ok: github-hosted-runner" comment
- **occystrap** (flake8wrap): Missing shellcheck disable=SC2086 directive
- **shakenfist** (flake8wrap): Missing shellcheck disable=SC2086 directive
- **shakenfist** (devpi fallback): 9 devpi-backed env block(s) missing a PIP_EXTRA_INDEX_URL pypi fallback: code-formatting.yml:27, codeql-analysis.yml:20, docs-tests.yml:19, functional-tests.yml:26, issue-fix.yml:102, publish-website.yml:17, release.yml:26, scheduled-tests.yml:24, test-drift-fix.yml:78. Add "PIP_EXTRA_INDEX_URL: https://pypi.org/simple/" alongside PIP_INDEX_URL so a devpi cold-cache miss (empty index for a first-touch package) falls back to pypi instead of failing with "from versions: none"

## Criteria with no automated check

These criteria are written down and judged by a person, so they have no table above. Each says why in its own page:

- [test-coverage.md](/components/development/audits/test-coverage/)
<!-- consistency-audit:end -->
