# Audit: Plan history in source

## What we check

Source code and configuration must not cite the numbered parts of an
implementation plan. A comment saying a branch was `added in phase 5`,
that a limit is the way it is `per decision 3`, or that a function is
`pending step 5f` records how the code came to be rather than why it is
the way it is. A reader of the code has not read the plan, the plan is
often finished, archived or not named at all, and the reasoning the
citation stands in for is exactly what the comment should have said.

The rule is the `plan-references-in-code` shared block, required in
every `PUSH-AUDIT.md` since 2026-09-24 (see the
[push-audit](/components/development/audits/push-audit/) audit). That block makes an added
reference a finding before pushing, and calls the references already
in the code backlog. This audit measures the backlog, so that it is
visible and shrinks instead of being rediscovered by each push audit.

The check reads the same files as `plan-source-references`: every file
`git ls-files` lists, less markdown (audited as documentation), files
under a plans directory in `docs/` at any depth (the record of how the
software was built, where citing plans is the point), anything that is
not a regular file inside the repository once symlinks are followed,
and files over 2 MB. On each line, case-insensitively, it looks for
four shapes:

| Shape | Example |
|-------|---------|
| A phase and its number | `phase 3`, `phases 2 and 3` |
| A hyphenated phase | `the phase-4 leaks pass` |
| A decision and its number | `decision 3`, `decision 6.4` |
| A step with a lettered number | `step 5f`, `steps 2a` |

The phase shape is `plan-phase-references`' pattern, shared rather
than copied. A step with a bare number, such as `step 3`, is not
matched: procedural comments write `step 1: open the file` in the
ordinary sense, and only the lettered form is a plan's. A phase with
no number (`two-phase commit`) is not matched either. Nor, for now, is
a phase or step whose number carries letters after the first, such as
`phase 3a` or `step 12ab`; that is a known gap rather than a judgment
that they are not plan history.

Before a line is matched, three things are removed from it:

* **Inline code spans**, as `plan-phase-references` removes them, so a
  comment can quote the shape it is talking about in backticks.
* **URLs**, which are addresses rather than prose.
* **Plan filenames** (`PLAN-*.md`). A plan whose phases are separate
  files carries the phase number in its name, as in
  `PLAN-test-harness-phase-05-direct-qemu.md`, and a pointer to one is
  not history written as prose. Whether it resolves is
  `plan-source-references`' business, and whether it should be there
  at all is the push audit's.

String literals are not removed. A log line announcing
`entering phase 3` is as much history as a comment saying it, and
more people read it. The exception is a literal the language writes
in backticks -- a JavaScript or TypeScript template literal, a shell
command substitution, a Go raw string. The code span removal cannot
tell those from a quoted shape in a comment, so they are removed
too, and a plan citation inside one passes. That errs towards
reporting too little, which is the safe direction for a check that
files issues.

### Exemptions

* **A line carrying `audit-ok: phase-reference`**, in whatever comment
  syntax the file uses (`# audit-ok: phase-reference`,
  `// audit-ok: phase-reference`). It is the same token
  `plan-phase-references` accepts as an HTML comment in
  documentation, for the rare line where the shape is genuinely not a
  plan reference -- a three-phase power supply, a regular expression
  describing the shape.
* **A file carrying `audit-ok: phase-reference-file`** once, near the
  top, with a sentence saying why. This is for a file made of the
  shapes rather than one that merely contains some: a test suite for
  this criterion has to write them as fixtures. It exempts the whole
  file, prose included, so prefer the line marker. A file carrying
  only the line marker is not exempted by it.
* **`mkdocs.yml`** (or `mkdocs.yaml`) at any depth. A plan with a page
  per phase puts a navigation entry per phase into it, titled with
  the phase number. That is documentation structure rather than a
  claim about code, and the pages it names are audited as
  documentation where they live.
* **Plans directories and markdown**, through the file walk this
  audit shares with `plan-source-references`.

Generated files are deliberately not exempt. A generated stub that
repeats a plan citation copied it from the comment in the source it
was generated from -- a `.proto`, an OpenAPI description -- so the fix
is at that source, and the generated file follows on the next
regeneration. Exempting generated files would leave the source
uncorrected and the citation shipping.

A repository with no source or configuration files outside markdown
and plans directories is N/A.

### How it composes

Three criteria and a grep divide the rule between them:

* `plan-phase-references` holds documentation to the same rule, for
  the phase shape only, in `README.md`, `AGENTS.md`,
  `ARCHITECTURE.md` and `docs/`. Its scope is documentation and it is
  deliberately unchanged, so its open issues keep meaning what they
  meant.
* `plan-source-references` reads the same files as this audit and asks
  a different question of them: whether each `PLAN-*.md` pointer
  resolves. A bare plan link may legitimately survive for work that
  is not built yet (`deferred; see PLAN-foo.md`), and telling that
  apart from `per PLAN-foo.md` is a reading of intent no regular
  expression makes, so plan filenames are that audit's and the push
  audit's, not this one's.
* This audit flags the numbered shapes, which are history by
  construction.
* This repository's wave 1 grep in `PUSH-AUDIT.md`, and the
  equivalent an adopting repository carries, catches the same shapes,
  plus plan filenames, on the lines a change adds, so that the backlog
  this audit reports stops growing while it is worked off. It matches
  `decision 6.4` by its first number and misses `phase 3a` just as
  this audit does, but it strips nothing and honours neither the
  markers nor the mkdocs exemption, so a hit is a candidate for the
  reviewer rather than a finding.

## Template

No template. Fix each reference at its source, by writing the reason
rather than deleting the citation: read the plan step it cites, and
replace the pointer with the constraint, measurement or failure it
stands for, in a sentence or two. Where the surrounding comment
already says why, drop the citation. Where a line uses the shape in
its ordinary sense, mark it `audit-ok: phase-reference`.

The one ordinary use the first fleet run found was a comment
labelling the stages of a procedure -- the release steps in a
`Makefile`, the log-collection stages of a workflow -- as `Phase 1:`,
`Phase 2:`. The pattern cannot exempt that shape, because real plan
history is written the same way about as often. Rename the labels
`Step 1:`, `Step 2:` instead, which the pattern deliberately does not
match, rather than marking each line.

## Projects

Per-project compliance for this criterion is regenerated
on every run of the consistency audit: see
[the compliance page](/components/development/audits/compliance/#plan-history-in-source).
