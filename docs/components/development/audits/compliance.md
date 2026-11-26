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
*Generated 2026-11-25T19:54:48.733652+00:00 from `scripts/audit-check.py`; do not edit.*

## bypass-actor-identity

Criterion: [bypass-actor-identity.md](/components/development/audits/bypass-actor-identity/)

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
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

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

- **client-python** (Status): pr-re-review.yml does not confirm that the tree it checked out is the commit it resolved, so a push between the two is reviewed with no warning; pr-retest.yml: job trigger-retest does not export pr-bot-trigger's same-repo output, so the job that needs it cannot require a same-repository pull request

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
| kerbside-client | compliant | - |
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
| kerbside-client | compliant | - |
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
| kerbside-client | compliant | - |
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
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | compliant | - |

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
| kerbside-client | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#30 |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **uncalibrated-sextant** (Status): Secret scanning not enabled; Secret scanning push protection not enabled

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
| client-python | non-compliant | shakenfist/client-python#457 |
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
| visual-digest-rust | non-compliant | shakenfist/visual-digest-rust#80 |

Details for non-compliant projects:

- **client-python** (Status): AGENTS.md is 318 lines / 2441 words (limits: 300 lines, 2500 words); move detail into docs/ and leave a summary and a link
- **visual-digest-rust** (Status): AGENTS.md has headings restating a docs/ page: "Design decisions" (docs/design-decisions.md); summarise and link instead

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
| kerbside-client | compliant | - |
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
| kerbside-client | compliant | - |
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
| clingwrap | compliant | - |
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

## moving-ref-source-protection

Criterion: [moving-ref-source-protection.md](/components/development/audits/moving-ref-source-protection/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
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
| shakenfist | N/A | - |
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
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | N/A | - |

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

- **client-python** (Status): 19 plan history reference(s) in source, in 3 file(s) (write the reason the code is this way instead of citing the plan step that produced it)
- **client-python-k3s** (Status): 64 plan history reference(s) in source, in 15 file(s) (write the reason the code is this way instead of citing the plan step that produced it)

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
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | N/A | - |

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
| private-ci | non-compliant | shakenfist/private-ci#304 |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | compliant | - |

Details for non-compliant projects:

- **client-python** (Status): 1 plan phase reference(s) in documentation (describe the current behaviour, or link the master plan in docs/plans/ instead of citing a phase number): AGENTS.md:142
- **private-ci** (Status): 10 plan phase reference(s) in documentation (describe the current behaviour, or link the master plan in docs/plans/ instead of citing a phase number): ARCHITECTURE.md:1585, docs/capacity-claims.md:248, docs/capacity-claims.md:249, docs/capacity-claims.md:1521, docs/capacity-claims.md:1554, docs/k3s-node-scaler.md:6, docs/k3s-node-scaler.md:12, docs/k3s-node-scaler.md:76, docs/k3s-node-scaler.md:171, docs/k3s-node-scaler.md:225

## plan-retirement

Criterion: [plan-retirement.md](/components/development/audits/plan-retirement/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | N/A | - |
| andris | compliant | - |
| client-python | non-compliant | shakenfist/client-python#459 |
| client-python-k3s | compliant | - |
| client-python-ova | compliant | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | non-compliant | shakenfist/divergulent#160 |
| hunkydory | N/A | - |
| images | N/A | - |
| instar | non-compliant | shakenfist/instar#958 |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | compliant | - |
| ryll | non-compliant | shakenfist/ryll#734 |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#29 |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **client-python** (Status): 1 plan(s) terminal for more than 90 days: PLAN-vdi-console-tokens.md (Complete, at least 91 days); 2 terminal plan(s) not yet eligible
- **divergulent** (Status): 2 plan(s) terminal for more than 90 days: PLAN-initial.md (Complete, at least 93 days), PLAN-prompt-injection-screening.md (Complete, at least 93 days); 2 terminal plan(s) not yet eligible; 3 row(s) already retired: PLAN-faster-full-run.md, PLAN-full-machine-run.md, PLAN-generated-marking.md
- **instar** (Status): 24 plan(s) terminal for more than 90 days: PLAN-375-guest-idt.md (Complete, at least 90 days), PLAN-amend.md (Complete, at least 90 days), PLAN-audit.md (Complete, at least 90 days), PLAN-bench-refcount-growth.md (Complete, at least 90 days), PLAN-bench.md (Complete, at least 90 days) (+19 more); 6 terminal plan(s) not yet eligible; 1 row(s) already retired: PLAN-dd.md
- **ryll** (Status): 20 plan(s) terminal for more than 90 days: PLAN-bug-reports.md (Complete, at least 90 days), PLAN-bugreport-trigger-snapshot.md (Complete, at least 90 days), PLAN-capture.md (Complete, at least 90 days), PLAN-cursor-rendering.md (Complete, at least 90 days), PLAN-display-draw-ops.md (Complete, at least 90 days) (+15 more); 2 terminal plan(s) not yet eligible; 6 row(s) already retired: PLAN-crate-extraction.md, PLAN-deferred-debt.md, PLAN-hamburger-menu.md, PLAN-initial.md, PLAN-pip-distribution.md (+1 more); 12 plan(s) in 1 table(s) with no Status column, which can never become eligible: OPEN-QUESTIONS.md, PLAN-crate-release.md, PLAN-display-iteration-followups.md, PLAN-macbook-bugreport-fixes.md, PLAN-main-event-drops.md (+7 more)
- **uncalibrated-sextant** (Status): 1 plan(s) terminal for more than 90 days: PLAN-first-playable.md (Complete, at least 120 days); 7 terminal plan(s) not yet eligible

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
| development | non-compliant | shakenfist/development#448 |
| divergulent | compliant | - |
| hunkydory | N/A | - |
| images | N/A | - |
| instar | non-compliant | shakenfist/instar#977 |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | non-compliant | shakenfist/private-ci#317 |
| ryll | compliant | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **client-python** (Status): 2 of 4 plan reference(s) in source or configuration do not resolve, in 2 file(s) (update the path, or use an absolute https://github.com/... URL for a plan in another repository)
- **development** (Status): 2 of 2 plan reference(s) in source or configuration do not resolve, in 1 file(s) (update the path, or use an absolute https://github.com/... URL for a plan in another repository)
- **instar** (Status): 1 of 134 plan reference(s) in source or configuration do not resolve, in 1 file(s) (update the path, or use an absolute https://github.com/... URL for a plan in another repository)
- **private-ci** (Status): 4 of 169 plan reference(s) in source or configuration do not resolve, in 3 file(s) (update the path, or use an absolute https://github.com/... URL for a plan in another repository)

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
| divergulent | compliant | - |
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
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **client-python-k3s** (Status): shared block plan-push-audit-phase is stale (v3 embedded, v4 current; copy it verbatim from templates/shared-blocks/plan-push-audit-phase.md in the development repository)

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

## push-audit

Criterion: [push-audit.md](/components/development/audits/push-audit/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | N/A | - |
| andris | compliant | - |
| client-python | N/A | - |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#175 |
| client-python-ova | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | non-compliant | shakenfist/divergulent#157 |
| hunkydory | compliant | - |
| images | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | non-compliant | shakenfist/occystrap#201 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#28 |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **client-python-k3s** (Status): shared block source-file-size is stale (v1 embedded, v2 current; copy it verbatim from templates/shared-blocks/source-file-size.md in the development repository)
- **divergulent** (Status): shared block source-file-size is stale (v1 embedded, v2 current; copy it verbatim from templates/shared-blocks/source-file-size.md in the development repository)
- **occystrap** (Status): shared block source-file-size is stale (v1 embedded, v2 current; copy it verbatim from templates/shared-blocks/source-file-size.md in the development repository)
- **uncalibrated-sextant** (Status): shared block source-file-size is stale (v1 embedded, v2 current; copy it verbatim from templates/shared-blocks/source-file-size.md in the development repository)

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
| kerbside-client | compliant | - |
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

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
| kerbside-client | compliant | - |
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

- **client-python** (Status): 1 release artifact(s) would ship docs/plans/, which is working history rather than source; exclude it with the mechanism each finding names

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
| kerbside-client | compliant | - |
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
| kerbside-client | compliant | - |
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
| kerbside-client | compliant | - |
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
| actions | non-compliant | shakenfist/actions#268 |
| agent-python | non-compliant | shakenfist/agent-python#159 |
| andris | non-compliant | shakenfist/andris#114 |
| client-python | non-compliant | shakenfist/client-python#437 |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#158 |
| client-python-ova | non-compliant | shakenfist/client-python-ova#64 |
| clingwrap | non-compliant | shakenfist/clingwrap#159 |
| cloudgood | non-compliant | shakenfist/cloudgood#26 |
| development | compliant | - |
| divergulent | non-compliant | shakenfist/divergulent#150 |
| hunkydory | non-compliant | shakenfist/hunkydory#175 |
| images | N/A | - |
| instar | non-compliant | shakenfist/instar#978 |
| kerbside | non-compliant | shakenfist/kerbside#770 |
| kerbside-client | non-compliant | shakenfist/kerbside-client#36 |
| kerbside-patches | non-compliant | shakenfist/kerbside-patches#2149 |
| library-utilities | non-compliant | shakenfist/library-utilities#104 |
| occystrap | non-compliant | shakenfist/occystrap#207 |
| private-ci | N/A | - |
| ryll | non-compliant | shakenfist/ryll#806 |
| sfui | non-compliant | shakenfist/sfui#84 |
| shakenfist | non-compliant | shakenfist/shakenfist#5079 |
| uncalibrated-sextant | non-compliant | shakenfist/uncalibrated-sextant#38 |
| visual-digest-rust | non-compliant | shakenfist/visual-digest-rust#83 |

Details for non-compliant projects:

- **actions** (Status): .github/workflows/renovate.yml exists, but the fleet runner in shakenfist/development manages this repository and a copy of the workflow enabled again races it; delete the file; renovate.json does not extend local>shakenfist/development:renovate-fleet, which carries the fleet's Renovate policy; add it to extends; renovate.json sets assignees, dependencyDashboard, gitAuthor, minimumReleaseAge, pre-commit, rollbackPrs, schedule at its top level, which local>shakenfist/development:renovate-fleet also sets, and a repository's own value wins over the preset's, so the fleet's policy does not apply here; delete them, and put any difference this repository needs in a packageRules entry scoped to what it is about
- **agent-python** (Status): .github/workflows/renovate.yml exists, but the fleet runner in shakenfist/development manages this repository and a copy of the workflow enabled again races it; delete the file; renovate.json does not extend local>shakenfist/development:renovate-fleet, which carries the fleet's Renovate policy; add it to extends; renovate.json sets assignees, dependencyDashboard, gitAuthor, minimumReleaseAge, pre-commit, rollbackPrs, schedule at its top level, which local>shakenfist/development:renovate-fleet also sets, and a repository's own value wins over the preset's, so the fleet's policy does not apply here; delete them, and put any difference this repository needs in a packageRules entry scoped to what it is about
- **andris** (Status): .github/workflows/renovate.yml exists, but the fleet runner in shakenfist/development manages this repository and a copy of the workflow enabled again races it; delete the file; renovate.json does not extend local>shakenfist/development:renovate-fleet, which carries the fleet's Renovate policy; add it to extends; renovate.json sets assignees, dependencyDashboard, gitAuthor, minimumReleaseAge, pre-commit, rollbackPrs, schedule at its top level, which local>shakenfist/development:renovate-fleet also sets, and a repository's own value wins over the preset's, so the fleet's policy does not apply here; delete them, and put any difference this repository needs in a packageRules entry scoped to what it is about
- **client-python** (Status): .github/workflows/renovate.yml exists, but the fleet runner in shakenfist/development manages this repository and a copy of the workflow enabled again races it; delete the file; renovate.json does not extend local>shakenfist/development:renovate-fleet, which carries the fleet's Renovate policy; add it to extends; renovate.json sets assignees, dependencyDashboard, gitAuthor, minimumReleaseAge, pre-commit, rollbackPrs, schedule at its top level, which local>shakenfist/development:renovate-fleet also sets, and a repository's own value wins over the preset's, so the fleet's policy does not apply here; delete them, and put any difference this repository needs in a packageRules entry scoped to what it is about
- **client-python-k3s** (Status): .github/workflows/renovate.yml exists, but the fleet runner in shakenfist/development manages this repository and a copy of the workflow enabled again races it; delete the file; renovate.json does not extend local>shakenfist/development:renovate-fleet, which carries the fleet's Renovate policy; add it to extends; renovate.json sets assignees, dependencyDashboard, gitAuthor, minimumReleaseAge, pre-commit, rollbackPrs, schedule at its top level, which local>shakenfist/development:renovate-fleet also sets, and a repository's own value wins over the preset's, so the fleet's policy does not apply here; delete them, and put any difference this repository needs in a packageRules entry scoped to what it is about
- **client-python-ova** (Status): .github/workflows/renovate.yml exists, but the fleet runner in shakenfist/development manages this repository and a copy of the workflow enabled again races it; delete the file; renovate.json does not extend local>shakenfist/development:renovate-fleet, which carries the fleet's Renovate policy; add it to extends; renovate.json sets assignees, dependencyDashboard, gitAuthor, minimumReleaseAge, pre-commit, rollbackPrs, schedule at its top level, which local>shakenfist/development:renovate-fleet also sets, and a repository's own value wins over the preset's, so the fleet's policy does not apply here; delete them, and put any difference this repository needs in a packageRules entry scoped to what it is about
- **clingwrap** (Status): .github/workflows/renovate.yml exists, but the fleet runner in shakenfist/development manages this repository and a copy of the workflow enabled again races it; delete the file; renovate.json does not extend local>shakenfist/development:renovate-fleet, which carries the fleet's Renovate policy; add it to extends; renovate.json sets assignees, dependencyDashboard, gitAuthor, minimumReleaseAge, pre-commit, rollbackPrs, schedule at its top level, which local>shakenfist/development:renovate-fleet also sets, and a repository's own value wins over the preset's, so the fleet's policy does not apply here; delete them, and put any difference this repository needs in a packageRules entry scoped to what it is about
- **cloudgood** (Status): .github/workflows/renovate.yml exists, but the fleet runner in shakenfist/development manages this repository and a copy of the workflow enabled again races it; delete the file; renovate.json does not extend local>shakenfist/development:renovate-fleet, which carries the fleet's Renovate policy; add it to extends; renovate.json sets assignees, dependencyDashboard, gitAuthor, minimumReleaseAge, pre-commit, rollbackPrs, schedule at its top level, which local>shakenfist/development:renovate-fleet also sets, and a repository's own value wins over the preset's, so the fleet's policy does not apply here; delete them, and put any difference this repository needs in a packageRules entry scoped to what it is about
- **divergulent** (Status): .github/workflows/renovate.yml exists, but the fleet runner in shakenfist/development manages this repository and a copy of the workflow enabled again races it; delete the file; renovate.json does not extend local>shakenfist/development:renovate-fleet, which carries the fleet's Renovate policy; add it to extends; renovate.json sets assignees, dependencyDashboard, gitAuthor, minimumReleaseAge, pre-commit, rollbackPrs, schedule at its top level, which local>shakenfist/development:renovate-fleet also sets, and a repository's own value wins over the preset's, so the fleet's policy does not apply here; delete them, and put any difference this repository needs in a packageRules entry scoped to what it is about
- **hunkydory** (Status): .github/workflows/renovate.yml exists, but the fleet runner in shakenfist/development manages this repository and a copy of the workflow enabled again races it; delete the file; renovate.json does not extend local>shakenfist/development:renovate-fleet, which carries the fleet's Renovate policy; add it to extends; renovate.json sets assignees, dependencyDashboard, gitAuthor, minimumReleaseAge, pre-commit, rollbackPrs, schedule at its top level, which local>shakenfist/development:renovate-fleet also sets, and a repository's own value wins over the preset's, so the fleet's policy does not apply here; delete them, and put any difference this repository needs in a packageRules entry scoped to what it is about
- **instar** (Status): .github/workflows/renovate.yml exists, but the fleet runner in shakenfist/development manages this repository and a copy of the workflow enabled again races it; delete the file; renovate.json does not extend local>shakenfist/development:renovate-fleet, which carries the fleet's Renovate policy; add it to extends; renovate.json sets assignees, dependencyDashboard, gitAuthor, minimumReleaseAge, pre-commit, rollbackPrs, schedule at its top level, which local>shakenfist/development:renovate-fleet also sets, and a repository's own value wins over the preset's, so the fleet's policy does not apply here; delete them, and put any difference this repository needs in a packageRules entry scoped to what it is about
- **kerbside** (Status): .github/workflows/renovate.yml exists, but the fleet runner in shakenfist/development manages this repository and a copy of the workflow enabled again races it; delete the file; renovate.json does not extend local>shakenfist/development:renovate-fleet, which carries the fleet's Renovate policy; add it to extends; renovate.json sets assignees, dependencyDashboard, gitAuthor, minimumReleaseAge, pre-commit, rollbackPrs, schedule at its top level, which local>shakenfist/development:renovate-fleet also sets, and a repository's own value wins over the preset's, so the fleet's policy does not apply here; delete them, and put any difference this repository needs in a packageRules entry scoped to what it is about
- **kerbside-client** (Status): .github/workflows/renovate.yml exists, but the fleet runner in shakenfist/development manages this repository and a copy of the workflow enabled again races it; delete the file; renovate.json does not extend local>shakenfist/development:renovate-fleet, which carries the fleet's Renovate policy; add it to extends; renovate.json sets assignees, dependencyDashboard, gitAuthor, minimumReleaseAge, pre-commit, rollbackPrs, schedule at its top level, which local>shakenfist/development:renovate-fleet also sets, and a repository's own value wins over the preset's, so the fleet's policy does not apply here; delete them, and put any difference this repository needs in a packageRules entry scoped to what it is about
- **kerbside-patches** (Status): .github/workflows/renovate.yml exists, but the fleet runner in shakenfist/development manages this repository and a copy of the workflow enabled again races it; delete the file; renovate.json does not extend local>shakenfist/development:renovate-fleet, which carries the fleet's Renovate policy; add it to extends; renovate.json sets assignees, dependencyDashboard, gitAuthor, minimumReleaseAge, pre-commit, rollbackPrs, schedule at its top level, which local>shakenfist/development:renovate-fleet also sets, and a repository's own value wins over the preset's, so the fleet's policy does not apply here; delete them, and put any difference this repository needs in a packageRules entry scoped to what it is about
- **library-utilities** (Status): .github/workflows/renovate.yml exists, but the fleet runner in shakenfist/development manages this repository and a copy of the workflow enabled again races it; delete the file; renovate.json does not extend local>shakenfist/development:renovate-fleet, which carries the fleet's Renovate policy; add it to extends; renovate.json sets assignees, dependencyDashboard, gitAuthor, minimumReleaseAge, pre-commit, rollbackPrs, schedule at its top level, which local>shakenfist/development:renovate-fleet also sets, and a repository's own value wins over the preset's, so the fleet's policy does not apply here; delete them, and put any difference this repository needs in a packageRules entry scoped to what it is about
- **occystrap** (Status): .github/workflows/renovate.yml exists, but the fleet runner in shakenfist/development manages this repository and a copy of the workflow enabled again races it; delete the file; renovate.json does not extend local>shakenfist/development:renovate-fleet, which carries the fleet's Renovate policy; add it to extends; renovate.json sets assignees, dependencyDashboard, gitAuthor, minimumReleaseAge, pre-commit, rollbackPrs, schedule at its top level, which local>shakenfist/development:renovate-fleet also sets, and a repository's own value wins over the preset's, so the fleet's policy does not apply here; delete them, and put any difference this repository needs in a packageRules entry scoped to what it is about
- **ryll** (Status): .github/workflows/renovate.yml exists, but the fleet runner in shakenfist/development manages this repository and a copy of the workflow enabled again races it; delete the file; renovate.json does not extend local>shakenfist/development:renovate-fleet, which carries the fleet's Renovate policy; add it to extends; renovate.json sets assignees, dependencyDashboard, gitAuthor, minimumReleaseAge, pre-commit, rollbackPrs, schedule at its top level, which local>shakenfist/development:renovate-fleet also sets, and a repository's own value wins over the preset's, so the fleet's policy does not apply here; delete them, and put any difference this repository needs in a packageRules entry scoped to what it is about
- **sfui** (Status): .github/workflows/renovate.yml exists, but the fleet runner in shakenfist/development manages this repository and a copy of the workflow enabled again races it; delete the file; renovate.json does not extend local>shakenfist/development:renovate-fleet, which carries the fleet's Renovate policy; add it to extends; renovate.json sets assignees, dependencyDashboard, gitAuthor, minimumReleaseAge, pre-commit, rollbackPrs, schedule at its top level, which local>shakenfist/development:renovate-fleet also sets, and a repository's own value wins over the preset's, so the fleet's policy does not apply here; delete them, and put any difference this repository needs in a packageRules entry scoped to what it is about
- **shakenfist** (Status): .github/workflows/renovate.yml exists, but the fleet runner in shakenfist/development manages this repository and a copy of the workflow enabled again races it; delete the file; renovate.json does not extend local>shakenfist/development:renovate-fleet, which carries the fleet's Renovate policy; add it to extends; renovate.json sets assignees, dependencyDashboard, gitAuthor, minimumReleaseAge, pre-commit, rollbackPrs, schedule at its top level, which local>shakenfist/development:renovate-fleet also sets, and a repository's own value wins over the preset's, so the fleet's policy does not apply here; delete them, and put any difference this repository needs in a packageRules entry scoped to what it is about
- **uncalibrated-sextant** (Status): .github/workflows/renovate.yml exists, but the fleet runner in shakenfist/development manages this repository and a copy of the workflow enabled again races it; delete the file; renovate.json does not extend local>shakenfist/development:renovate-fleet, which carries the fleet's Renovate policy; add it to extends; renovate.json sets assignees, dependencyDashboard, gitAuthor, minimumReleaseAge, pre-commit, rollbackPrs, schedule at its top level, which local>shakenfist/development:renovate-fleet also sets, and a repository's own value wins over the preset's, so the fleet's policy does not apply here; delete them, and put any difference this repository needs in a packageRules entry scoped to what it is about
- **visual-digest-rust** (Status): .github/workflows/renovate.yml exists, but the fleet runner in shakenfist/development manages this repository and a copy of the workflow enabled again races it; delete the file; renovate.json does not extend local>shakenfist/development:renovate-fleet, which carries the fleet's Renovate policy; add it to extends; renovate.json sets assignees, dependencyDashboard, gitAuthor, minimumReleaseAge, pre-commit, rollbackPrs, schedule at its top level, which local>shakenfist/development:renovate-fleet also sets, and a repository's own value wins over the preset's, so the fleet's policy does not apply here; delete them, and put any difference this repository needs in a packageRules entry scoped to what it is about

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
| actions | compliant | - |
| agent-python | N/A | - |
| andris | non-compliant | shakenfist/andris#58 |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| client-python-ova | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | non-compliant | shakenfist/development#449 |
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
| ryll | compliant | - |
| sfui | non-compliant | shakenfist/sfui#42 |
| shakenfist | N/A | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **andris** (Status): 9 of 140 in-scope files reviewed at HEAD (9 imported from shakenfist/development); 131 need review (threshold 5)
- **development** (Status): 236 of 253 in-scope files reviewed at HEAD; 17 need review (threshold 5)
- **kerbside** (Status): 129 of 378 in-scope files reviewed at HEAD (7 imported from shakenfist/development); 249 need review (threshold 5)
- **kerbside-client** (Status): 7 of 30 in-scope files reviewed at HEAD (7 imported from shakenfist/development); 23 need review (threshold 5)
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
| kerbside | compliant | - |
| kerbside-client | compliant | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | N/A | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

## review-tracking-templates

Criterion: [review-tracking-templates.md](/components/development/audits/review-tracking-templates/)

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
| kerbside | compliant | - |
| kerbside-client | compliant | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | N/A | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

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
| uncalibrated-sextant | compliant | - |
| visual-digest-rust | compliant | - |

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

## security-sanitization

Criterion: [security-sanitization.md](/components/development/audits/security-sanitization/)

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| andris | N/A | - |
| client-python | non-compliant | shakenfist/client-python#458 |
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

Details for non-compliant projects:

- **client-python** (Status): 1 of 1 HTTP request handler class(es) do not sanitize header values: shakenfist_client/commandline/auth.py:620 (_RedirectHandler): does not inherit SafeHeaderMixin, so send_header() passes CR and LF straight through

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
| kerbside | non-compliant | shakenfist/kerbside#771 |
| kerbside-client | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | non-compliant | shakenfist/private-ci#305 |
| ryll | non-compliant | shakenfist/ryll#807 |
| sfui | N/A | - |
| shakenfist | N/A | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

Details for non-compliant projects:

- **kerbside** (Status): kerbside/api/static/sfui: 1 commit(s) behind canonical; re-run tools/vendor.sh from an up to date sfui checkout
- **private-ci** (Status): conductor/static/sfui: 41 commit(s) behind canonical; re-run tools/vendor.sh from an up to date sfui checkout
- **ryll** (Status): ryll/src/web/assets/sfui: 1 commit(s) behind canonical; re-run tools/vendor.sh from an up to date sfui checkout

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
| kerbside-client | compliant | - |
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
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | compliant | - |
| hunkydory | N/A | - |
| images | N/A | - |
| instar | N/A | - |
| kerbside | compliant | - |
| kerbside-client | compliant | - |
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
| uncalibrated-sextant | N/A | - |
| visual-digest-rust | N/A | - |

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
| clingwrap | compliant | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | N/A | - |
| cloudgood | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | N/A | - |
| development | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | compliant | N/A | - |
| divergulent | compliant | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | N/A | - |
| hunkydory | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | compliant | N/A | - |
| images | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | - |
| instar | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | N/A | - |
| kerbside | compliant | compliant | compliant | compliant | compliant | compliant | compliant | compliant | compliant | compliant | - |
| kerbside-client | compliant | compliant | compliant | compliant | compliant | compliant | N/A | compliant | compliant | N/A | - |
| kerbside-patches | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | N/A | - |
| library-utilities | compliant | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | N/A | - |
| occystrap | compliant | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | N/A | - |
| private-ci | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | - |
| ryll | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | compliant | N/A | - |
| sfui | N/A | compliant | compliant | compliant | compliant | compliant | compliant | compliant | compliant | compliant | - |
| shakenfist | compliant | compliant | compliant | compliant | compliant | compliant | compliant | compliant | N/A | N/A | - |
| uncalibrated-sextant | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | N/A | - |
| visual-digest-rust | N/A | compliant | compliant | compliant | compliant | compliant | N/A | compliant | N/A | N/A | - |

Details for non-compliant projects:

- **client-python** (VM size): 3 "vm" runner job(s) naming no size: code-formatting.yml:19 (self-hosted, vm), functional-tests.yml:102 (self-hosted, vm), supply-chain.yml:81 (self-hosted, vm). The conductor takes the runner size from the labels and falls back to the first CI_SIZES entry -- "xs", one vCPU and 2048 MB -- when it finds none, so an omitted size is a silent downgrade to the smallest runner rather than a free choice. Add the size the job actually wants (xs/s/m/l/xl; m-bigdisk/xl-bigdisk when the job needs the disk; s-cloud/s-cluster when it builds a nested cloud or cluster); "xs" is a valid answer stated explicitly. A job which genuinely cannot name one marks the line "audit-ok: vm-runner-size" with the reason

## Criteria with no automated check

These criteria are written down and judged by a person, so they have no table above. Each says why in its own page:

- [test-coverage.md](/components/development/audits/test-coverage/)
<!-- consistency-audit:end -->
