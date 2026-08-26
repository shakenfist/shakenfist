# Audit: LLM context linting in pre-commit and CI

## What we check

The daily consistency audit is a backstop, not a feedback loop. A
malformed skill, a smuggled unicode character or a secret pasted into
`CLAUDE.md` should be caught by the commit that introduces it, not up
to twenty-four hours later by a report nobody is watching.

Every repository with agent context must therefore run
[skillsaw](https://skillsaw.org/) itself, in both places the other
linters run:

* `.pre-commit-config.yaml` runs the skillsaw hook, so the feedback
  arrives before the commit exists.
* A CI workflow runs skillsaw, so the check cannot be skipped with
  `--no-verify` or by a clone that never ran `pre-commit install`.

Both are required. Pre-commit alone is advisory; CI alone is slow.

A CI job running `pre-commit run` satisfies the second half without
naming skillsaw, because it runs every hook the config declares.
Requiring the linter to be named in a workflow as well would report a
repository non-compliant for a wiring that does run it. The pre-commit
half is still checked independently, so a workflow running pre-commit
against a config with no skillsaw hook does not pass.

As with the secret scanner check, *how* skillsaw is invoked is
deliberately not pinned: naming it in a pre-commit config and in a
workflow is the step change, and requiring a particular rev or argument
list would make the audit brittle against reasonable variation.

## Template

Pre-commit, alongside the existing actionlint, shellcheck and flake8
hooks:

```yaml
  - repo: https://github.com/stbenjam/skillsaw
    rev: v0.18.0
    hooks:
      - id: skillsaw
```

The hook runs `skillsaw lint`, which fails on error severity only --
the same tier the [llm-context-lint](/components/development/audits/llm-context-lint/) audit
reports, so the two cannot disagree about what counts as broken.

CI, in the lane that already runs the other linters:

```yaml
      - uses: stbenjam/skillsaw@v0
```

Pin `rev` and the action to a commit SHA if the repository pins its
other third-party actions that way. Repositories that enable renovate's
pre-commit manager will have the `rev` kept current automatically.

## Projects

<!-- consistency-audit:begin -->
*Generated 2026-08-26T06:56:26.297909+00:00 from `scripts/audit-check.py`; do not edit.*

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
<!-- consistency-audit:end -->
