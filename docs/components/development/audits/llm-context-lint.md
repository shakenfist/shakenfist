# Audit: LLM context linting

## What we check

Agent context -- `AGENTS.md`, `CLAUDE.md`, skills, plugins, hooks and
MCP configuration -- is code an agent executes against. This audit runs
[skillsaw](https://skillsaw.org/) over each repository and reports
error-severity findings, plus one structural check skillsaw cannot
make.

### skillsaw at error severity

Only the error tier is reported. skillsaw's warning and info tiers
carry style opinions -- unlinked path references alone run to dozens
per repository -- and an audit reporting them would spend more of our
time than it saves. The error tier is the structural and security
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

The error tier was empty in every repository when this audit was
written, so a failure here is a regression rather than a backlog.

### Markdown that will never load as a skill

A skill is `<skills dir>/<name>/SKILL.md`. A bare markdown file
directly in `.claude/skills/`, or a subdirectory with no `SKILL.md`, is
inert: the agent does not load it, and skillsaw does not lint it
either, because it is never discovered as a skill. A repository in that
state lints clean while its skills do nothing.

This is not hypothetical: when the audit was written twelve local
checkouts were affected -- including instar, whose `AGENTS.md` asserts
"Custom skills in `.claude/skills/` cover the repetitive work" -- and
every one of them was passing the `llm-tooling` audit at the time.
Sampled across those repositories the check produced no false
positives: every file flagged either opens with "Use this skill when
..." or describes itself as a slash command. `README.md` and `index.md`
are allowed to sit beside skill directories.

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
`<name>/SKILL.md` with `name` and `description` frontmatter, or, if the
file is really a slash command, to `.claude/commands/`, where flat
markdown is the correct shape.

See [llm-context-lint-ci.md](/components/development/audits/llm-context-lint-ci/) for running the
same linter per commit rather than once a day.

## Projects

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](/components/development/audits/compliance/#llm-context-lint).
