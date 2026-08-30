# Audit: diagram format

## What we check

A diagram of *structure or flow* -- boxes and the arrows between them,
an ordered exchange of messages, a state machine -- is written as a
fenced `mermaid` block rather than drawn by hand in characters.
GitHub renders those natively, and the mkdocs sites render them
through `pymdownx.superfences`, so one source is a picture in both
places. Drawn by hand it is a picture nowhere, and it stops being
editable the moment a box needs a longer label.

The fleet was split down the middle when this was written: three
repositories used mermaid and the rest had not started, with
nineteen hand-drawn diagrams between them.

Scope is the documentation content -- `README.md`, `AGENTS.md`,
`ARCHITECTURE.md` and `docs/`, minus plans directories and minus a
repository's `doc_content_excludes` prefixes, the same scope
`plan-phase-references` uses. Plans are deliberately out: a plan is a
working document read by the people writing it, and sweeping a
repository's plan history for diagrams is archaeology rather than
maintenance. The excludes matter for shakenfist, whose
`docs/components/` is synced from the component repositories by
`sync-external-docs.yml` -- a diagram fixed there is reverted by the
next sync, so it has to be fixed at its source.

### What is not a diagram

Most character art in these repositories is not a diagram, and
converting it would destroy it. These stay as plain code fences:

* directory and file trees;
* memory maps, address-space layouts, and register or bit-field
  diagrams, where column alignment carries the meaning;
* wire-format and on-disk byte layouts;
* captured terminal output, and tables.

The check separates them with three signals, and is deliberately
conservative about all of them. A false positive files an issue
against a repository whose documentation is correct; a false negative
costs nothing, because the `diagram-discipline` shared block puts a
human reviewer in front of every new diagram anyway.

* **A corner.** `┌`, `┐`, or a `+---+` rule. A file tree uses tees and
  elbows but never a top corner, which is what tells ryll's annotated
  `src/` listing from a drawn box.
* **An edge.** A solid triangular arrowhead (`▼`, `▶`, `◄`), or a rule
  of at least two characters ending in an angle bracket (`--->`,
  `───▶`). Thin single arrows -- up, down, left, right -- are
  deliberately not edges. In this fleet they are overwhelmingly
  annotation pointers: the head and tail markers under a ring buffer,
  or `0x08: data_gpa (u64)  ← guest phys addr` inside a register map.
  Counting them turned two memory maps into diagrams.
* **A flow connector.** A line drawn from nothing but connector
  glyphs, carrying a downward arrowhead, optionally with a
  parenthetical label. Only the downward forms count: a caret is the
  callout character in a bit-field diagram, where a row of them points
  up at the fields above.

A block with three or more rows beginning with a hex offset is a
memory map, and is skipped before any of that runs.

## Exemptions

A block that is genuinely better drawn by hand -- and there are real
ones -- is exempted with a comment on the fence line or the line above
it:

```markdown
<!-- audit-ok: diagram-format -->
```

The marker is accepted on the fence line itself or on the nearest
non-blank line above it, so the conventional blank line between an
HTML comment and the block it applies to is fine. Only blank lines are
skipped: a marker cannot be inherited from a paragraph further up that
was talking about a different diagram.

Say in the surrounding prose why, the way the other `audit-ok` markers
in this fleet are used. The marker is not a way to close an issue
without reading the block.

## How to fix it

The `diagram-conversion` skill in the development repository's
`.claude/skills/` is the procedure: how to find candidates, which to
leave alone, how to pick between `flowchart`, `sequenceDiagram`,
`stateDiagram-v2` and `erDiagram`, and how to verify the result
renders. Verifying is not optional -- mermaid fails at render time,
which is what the `mermaid-lint-ci` audit is for.

## Template

No template -- the diagrams are project-specific. The policy a
reviewer applies to a diff is the `diagram-discipline` shared block,
`templates/shared-blocks/diagram-discipline.md`, embedded in each
repository's `PUSH-AUDIT.md` and checked by the `push-audit` audit.

## Projects

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](/components/development/audits/compliance/#diagram-format).
