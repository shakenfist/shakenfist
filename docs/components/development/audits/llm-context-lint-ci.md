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

A workflow that installs skillsaw from PyPI and then invokes the
`skillsaw` command satisfies the second half as well, without naming
the upstream repository anywhere. Where the command sits is not
pinned: on its own line inside a `run: |` block, inline in a
single-command `run:` step, after a shell operator, or reached through
`uvx`, `uv run`, `python -m`, or a path into a virtualenv.

What does not count is naming the package without running it. An
install line -- `pip install skillsaw==0.18.0`, including the wrapped
form that leaves the pin on a line of its own -- a job or key called
`skillsaw:`, and a `skillsaw --version` probe asserting the install
worked are all mentions rather than runs. Installing a linter is not
running it, and neither is asking it what version it is.

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

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](/components/development/audits/compliance/#llm-context-lint-ci).
