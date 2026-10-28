# Retiring finished plans

A plan in `docs/plans/` is written to get one piece of work done.
Once that work is finished, the plan is *retired*: what it knew that
is still worth knowing is moved to where it will be maintained, and
its files are deleted. This page defines when a plan is eligible and
what retiring one involves.

## Why

Nothing removes a finished plan, so `docs/plans/` only grows. In
October 2026 shakenfist and ryll each held over 180 plan files --
more than 100,000 lines in shakenfist -- and ryll had 23 plans that
had been finished for more than 90 days. Every agent session that
searches a repository wades through plans describing code that has
since been rewritten, and treats them as current. Worse, what a
finished plan holds that the documentation does not -- why the code
is that shape, which designs were tried and rejected, the work it
deferred -- is never read again, so it is lost while still sitting
in the tree.

## Eligibility

A plan is eligible for retirement when both of these hold:

* its row in `docs/plans/index.md` has the status `Complete`,
  `Superseded` or `Abandoned`; and
* no commit has touched the master plan, or any of its phase files,
  for 90 days.

The clock is the last commit rather than a date written into the
plan, so plans do not have to start recording anything new, and a
plan whose push-audit follow-ups are still landing is not yet
eligible. `Superseded` and `Abandoned` plans retire on the same
clock; for an abandoned plan, the reasoning worth keeping is usually
why it was abandoned.

## What retiring involves

1. **`docs/` describes what the plan built.** Usually it already
   does; where it does not, the gap is filled.
2. **The plan's reasoning is written down where it will be
   maintained** -- in `docs/`, or in `ARCHITECTURE.md` where it is
   about the shape of the system. That means the constraints it
   verified, the measurements it took, the alternatives it rejected
   and why, and any push-audit findings it declined in writing.
   This is the part most easily lost, and the part whose loss costs
   most: it is how the next change avoids re-proposing a design that
   has already failed.
3. **Each *Future work* item still worth doing becomes a GitHub
   issue**, labelled `from-retired-plan`. The issues are filed while
   the pull request is prepared and listed in its body; if the pull
   request is closed unmerged, they are closed again. Items dropped
   instead are listed in the body too, each with a reason.
4. **Every pointer to the plan is rewritten**, to the new home of
   whatever it was pointing at, or to the permalink below. Search for
   each file's basename (`git grep -n PLAN-example.md`), master and
   phase files alike. Two kinds of pointer are caught if missed:
   relative links inside `docs/` are reported by
   [`docs-external-links`](/components/development/audits/docs-external-links/), and plan
   references in source by
   [`plan-source-references`](/components/development/audits/plan-source-references/).
   Nothing catches the rest, which become silent 404s and stale
   names: absolute `blob/<branch>` links in `README.md`, and plan
   names in backticks in `AGENTS.md`, `ARCHITECTURE.md`, other plans
   and `.claude/`.
5. **The master plan and its phase files are deleted.**
6. **The index row stays.** It keeps its date, intent and status,
   and its link becomes a permalink to the plan's final text:

   ```
   https://github.com/shakenfist/<repo>/blob/<sha>/docs/plans/<file>
   ```

   where `<sha>` is

   ```
   git log -1 --format=%H origin/<default> -- docs/plans/<file>
   ```

   That is the last commit on the default branch that changed the
   file, so it holds the final text and, being on the default
   branch, is never garbage collected. Never link `blob/main`: once
   the file is deleted that is a 404. The index stays the complete
   history of what the repository has planned, and `git` keeps the
   text. A row whose link is an absolute URL registers no local
   plan, so [`plan-index`](/components/development/audits/plan-index/) accepts a retired
   row as it stands.

## What it is not

* **Retired is not a status.** A retired plan's row still reads
  `Complete`, `Superseded` or `Abandoned`. A status says whether a
  plan still wants attention, and a retired plan does not; that its
  text now lives only in history is already shown by the link.
* **A plan whose reasoning cannot be placed is not retired.** If
  there is no sensible home in `docs/` for why the code is the shape
  it is, the plan is left where it is and reported, so that a person
  can decide. Deleting the only copy of a decision's justification
  to meet a 90-day rule is exactly the loss retirement exists to
  prevent.
* **It is never automatic.** Retirement always goes through a
  reviewed pull request: one per repository, one commit per plan,
  merged by a person. It is deliberately not part of the
  `prune-reviews` workflow, which pushes straight to the default
  branch and is safe only because what it does is computable.
  Deciding where a plan's reasoning belongs is not.

## History

ryll's `docs/plans/completed/` was an earlier approach: finished
phase files were moved there by hand and kept in the tree.
Retirement replaces it; the files in it retire like any others.

The `retire-plans` skill in the private `llm-setup` repository is
what does this, once it is written; it does not exist yet.
