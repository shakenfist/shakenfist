# Audit: Push audit phase in master plans

## What we check

Every master plan ends with a phase that runs the repository's
`PUSH-AUDIT.md` over the accumulated diff of the whole plan. The rule
is the `plan-push-audit-phase` shared block, and this criterion is what
holds it in place: the phase reached the fleet's plans as a hand-driven
sweep, and until this check existed nothing stopped the next plan from
omitting it.

For every master plan `docs/plans/index.md` links whose status is not
terminal -- that is, anything other than `Complete`, `Abandoned` or
`Superseded`:

* the plan **names `PUSH-AUDIT.md`**, and
* the **last** of its phases is the push audit phase.

Last is the part of the rule that matters and the part that rots. An
audit scheduled in the middle of a plan is outrun by the phases that
follow it, and the plan reaches `Complete` with work nothing ever
audited -- which is not hypothetical: the check finds plans whose audit
phase was overtaken by phases appended after it.

Phases are read from the plan file, never from the index. Index layouts
differ across the fleet by design -- one repository carries a phase
count column, another an inline list of phase names, this one no phase
column at all -- so anything keyed on an index column would be
unimplementable in half of it. What the check reads from the index is
the pair every layout agrees on: which plans it links, and what each
one's status cell says.

Links that leave the repository are not read at all. An index may
point at a plan elsewhere -- a superseded-by, or a mirror -- and
nothing under this repository's `docs/plans/` can answer for it.
Resolving such a link locally judged a same-named local file as though
the index had linked it, and where no local file matched, named the
plan in the verdict as one whose file is missing, which reads as a
broken link when the link is fine.

A phase is a row of a table whose first column is `Phase`, or a
numbered heading. A heading that names itself a phase, as in
`### Phase 5: Push audit`, is read wherever it sits in the document;
a bare `### 5. Push audit` is read only inside a section headed
execution, implementation, phases or workstreams, because plans also
number ordinary subsections and a list of numbered findings is not a
list of phases. Both shapes are read where a plan carries both, keyed by phase
number rather than by position, because the numbers agree where
document order does not. A heading that names itself a phase may omit
its title -- `### Phase 1`, `### Phase 2` is a shape the fleet writes,
and such a plan had no phases the check could read at all -- and its
section's first paragraph then says what the phase is, the way the
rest of the row answers for a bare `Phase` cell. Only that explicit
form may omit a title: a heading that is a bare number and nothing
else is the numbered subsection above, not a phase.

Fenced code blocks are not read as document structure. A plan is
allowed to show what a phase section or an Execution table looks like,
and a runbook snippet is allowed to contain a `#` comment; none of
that is the plan's own structure. Reading it as structure invented
phases out of examples and, worse, silently unphased a plan whose
Execution section contained a shell snippet -- the comment popped the
heading stack and took the phase table below it out of the section.
The same applies to `docs/plans/index.md`: a link shown inside a fence
registers no plan.

Where a plan carries both, a numbered heading is read as a phase's own
section only when its name begins with the table row's. The heading
`### 2. Deploy` belongs to the row `2. Deploy`; the heading
`### 1. Foundations -- this repository` belongs to the row
`1. Foundations`; and the heading `### 1. A defect we noticed` belongs
to neither. What that protects is a plan that has run its audit and
written the findings up as a numbered list -- the shape a plan takes
once the phase this criterion asks for has done its job. Its findings
are numbered from one exactly as its phases are, and reading them as
phase sections reported the plan for placing its audit above its own
findings.

Which phase is the audit is read from the phase's own name where it
has one. A `Phase` column holding a bare number -- `8`, or `Phase 8`
-- names nothing, and there the whole row is read instead, because
that is where such a plan describes its phases. So a Notes column
mentioning a push audit passes a numbers-only table and does not pass
a table whose phases are named. Where such a row has a numbered
section heading of its own, that heading's title is read alongside the
row, because a plan whose `Phase` column is bare and whose phase names
live in its section headings names them nowhere else.

There is a third shape the check accepts: a plan that ends with a
bare `## Push audit` section rather than a numbered phase, which is
how a plan numbered before the convention arrived carries the audit.
It counts as the final phase when it sits after the last phase's own
content -- that phase's section heading where the plan writes one, and
otherwise the table row that introduces it. A plan whose audit section
sits above the section describing its final phase is an outrun audit
like any other and is reported as one.

That heading must read exactly `Push audit`. A section headed
`Push audit findings` is the record of an audit that ran, rather than
a phase, and is not counted as one. A heading that merely starts with
those words is named in the finding instead of passed over, because a
message saying the plan has no push audit phase would deny a heading
its author can see on the page.

Repositories with no `docs/plans/index.md` are N/A. Whether every
project should plan this way is a separate decision, made by the
`plan-index` criterion rather than here. So is a repository whose
index links no master plan file the check can find: there is nothing
to judge, and the broken links are `docs-external-links`' finding
rather than this one's. A plan the check could not find is *named* in
the result either way, because a plan silently walked past looks
exactly like a plan that passed.

## What this deliberately does not cover

* **Whether the audit was run.** A plan can carry the phase, never run
  it, and stay green. The check measures presence, which is all a grep
  can measure. What catches an unrun audit is the plan not being
  markable `Complete`, which is a human gate and stays one.
* **Plans with a terminal status that do not carry the phase.** They
  pass. This is the shared block's carve-out, not an oversight: a plan
  whose work has landed, or that was deliberately dropped or replaced,
  is not reopened to acquire a phase that would audit a diff nobody is
  going to write. It is also the difference between a check that names
  the handful of plans still able to act on a finding and one that
  files an issue against every plan the fleet has ever closed.

  The block words the carve-out as `Complete` alone. The check applies
  it to all three terminal terms of the status vocabulary, because
  `Abandoned` and `Superseded` are terminal for the same reason and
  the block's silence about them is a gap rather than a decision.
  Rewording the block bumps its version and stales every embedded copy
  across the fleet, which is a sweep; it is scheduled as one, in
  `docs/plans/PLAN-push-audit-phase.md`.
* **Plans with a terminal status that do carry the phase.** Not
  inspected either. Whether the audit ran is a judgement about the
  plan's own record, and the presence of a heading cannot settle it.
* **Plans with no phases the check can read.** Follow-up lists, issue
  trackers and single-commit plans are written without an Execution
  table. There is no last phase for the rule to bind, and inventing
  one would report work nobody ever phased.
* **Phases filed under a heading the check does not recognise.** A
  plan can carry phases the check cannot see, and such a plan passes.
  The list of phase-bearing headings is empirical rather than
  principled: it is the set the fleet has been observed to write, not
  a rule anybody agreed to in advance. divergulent's
  `PLAN-release-1.0.md` is the case that taught us this -- eight
  numbered sections under `## Must-do workstreams`, tracked as phases
  in that repository's index, invisible to the check until
  `workstreams` was added to the list. There will be another shape.

  This is why both the pass and the fail message *name* the plans the
  check declined to judge rather than counting them. Failing them is
  not the answer -- it would fail ryll's standalone issue-tracking
  plans, which are legitimately unphased -- but a verdict that hides
  them is how the next `PLAN-release-1.0.md` stays hidden. The names
  are there so a person can read the handful by hand.
* **Repositories with no plan practice.** No index, no finding.

## Template

No template of its own. The canonical wording of the phase is the
`plan-push-audit-phase` shared block in
`templates/shared-blocks/plan-push-audit-phase.md`, which every
`PLAN-TEMPLATE.md` must carry -- that is the `plan-template`
criterion's business, so a repository whose template lacks the block
and whose plans lack the phase is told both things once each, by the
criterion that owns each.

To fix a finding where the plan has no audit phase at all, add one as
the last row of the plan's Execution table (or as its last phase
section), and say in it that it runs `PUSH-AUDIT.md` over the
accumulated diff of every phase in the plan against the default branch.

Where the plan has an audit phase that later phases have overtaken,
which fix applies depends on whether that audit has run, and the
finding says which:

* **The audit has not run** -- its own status is not terminal. The
  phases are simply in the wrong order: move the audit phase after the
  ones it must audit, leaving one audit phase.
* **The plan records no status for the audit.** A phase written as a
  numbered section carries no Status cell, and a trailing
  `## Push audit` section carries none either -- that is what makes it
  a section rather than a phase. For both shapes there is nothing to
  read and either fix may be the right one. The finding says so and
  names both rather than asserting the reorder: the reorder is the
  destructive guess, because a plan whose audit has already run gets a
  false record of what was audited out of it. Reading the plan settles
  which applies in a few seconds; guessing wrong costs a round trip on
  a repository nobody here is watching.
* **The audit has run** -- its status is terminal, and there are
  phases after it. The check cannot tell whether that work landed
  after the audit ran or was always scheduled behind it, so the
  finding states what it sees and leaves the fix conditional on what
  you know. Where the work did land afterwards, append a *new* audit
  phase covering it: moving the finished phase to the end would claim
  it audited work that landed after it, which is a false record of
  what was audited, and the plan ending with two audit phases is
  correct, because there were two bodies of work. Where the work was
  always behind the audit, the audit was scheduled in the wrong place
  and the phases want reordering as above.

## Projects

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](/components/development/audits/compliance/#plan-audit-phase).
