# Audit: LLM context linting

## What we check

Agent context -- `AGENTS.md`, `CLAUDE.md`, skills, plugins, hooks and
MCP configuration -- is code that an agent executes against, but
nothing has been checking it. This audit runs
[skillsaw](https://skillsaw.org/) over each repository and reports
error-severity findings, plus one structural check skillsaw cannot
make.

### skillsaw at error severity

Only the error tier is reported. skillsaw's warning and info tiers
carry style opinions -- unlinked path references alone run to dozens
per repository -- and an audit that reported them would spend more of
our time than it saves. The error tier is the structural and security
subset:

* `agentskill-valid`, `claude-plugin-json-valid` and friends --
  malformed manifests and frontmatter.
* `content-embedded-secrets` -- credentials in instruction files. The
  `secret-scanning-ci` check covers only that a scanner runs; this
  covers the files that scanner is least likely to be pointed at.
* `security-invisible-unicode` -- Trojan Source and ASCII smuggling in
  files an agent obeys.
* `hooks-dangerous`, `claude-settings-dangerous` -- settings and hooks
  that execute arbitrary commands.

Measured across shakenfist, instar, kerbside, occystrap, development
and kerbside-patches when this audit was written, the error tier was
empty in every repository. The baseline is green, so a failure here is
a regression rather than a backlog.

### Markdown that will never load as a skill

A skill is `<skills dir>/<name>/SKILL.md`. A bare markdown file
directly in `.claude/skills/`, or a subdirectory with no `SKILL.md`,
is inert: the agent does not load it, and skillsaw does not lint it
either, because it is never discovered as a skill at all. A repository
in that state lints clean while its skills do nothing.

This is not hypothetical. When the audit was written, twelve local
checkouts were affected, including instar (12 files, whose `AGENTS.md`
asserts "Custom skills in `.claude/skills/` cover the repetitive
work"), kerbside, occystrap, shakenfist and kerbside-patches. Every
one of those repositories was passing the `llm-tooling` audit at the
time.

Sampled across those repositories the check produced no false
positives: every file flagged either opens with "Use this skill when
..." or describes itself as a slash command. `README.md` and
`index.md` are allowed to sit beside skill directories.

### Not applicable

* Repositories with no agent context files at all.
* A missing skillsaw binary. That is the audit harness's problem, not
  the audited repository's, and failing would file an issue against
  every project in the fleet for something none of them can fix. The
  consistency-audit workflow installs a pinned skillsaw, so the state
  should not arise; when it does, every row flipping to N/A at once is
  the signal.

## Template

No template -- the fix is repository-specific. Skills move to
`<name>/SKILL.md` with `name` and `description` frontmatter, or, if
the file is really a slash command, to `.claude/commands/`, where flat
markdown is the correct shape.

See [llm-context-lint-ci.md](/components/development/audits/llm-context-lint-ci/) for running the
same linter per commit rather than once a day.

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-08-24T07:04:16.593679+00:00

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
<!-- consistency-audit:end -->
