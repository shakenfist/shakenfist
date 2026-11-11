# Audit: Plan retirement

## What we check

A plan in `docs/plans/` is written to get one piece of work done, and
nothing removes it when that work is finished, so `docs/plans/` only
grows. [docs/plan-retirement.md](/components/development/plan-retirement/) is the
convention that ends that: a plan whose index row has carried a
terminal status for more than 90 days has its reasoning moved into
`docs/`, its remaining *Future work* filed as issues and its files
deleted, leaving its index row pointing at a permalink. This criterion
finds the plans that are due.

A repository with no `docs/plans/index.md` is N/A: the index is what
records a status, and a repository that does not plan this way has
nothing to measure. Where there is one, the criterion fails naming
every plan whose row is `Complete`, `Superseded` or `Abandoned` and
has been for longer than 90 days.

### The clock

The clock starts at the commit where the row changed into a terminal
status from one that was not, read from the history of
`docs/plans/index.md`. It is never the last commit to touch the plan's
files, and the plan's own files play no part in it. Finished plans are
edited all the time by changes that are not about them -- a sweep
making links absolute, a template fix, retirement itself rewriting the
pointers in other plans -- so a clock that restarted on every touch
would never run out, and the plans most worth retiring are exactly the
ones those sweeps keep touching.

Only a change *into* a terminal status starts it. A plan reopened and
finished again starts a new clock. A move from one terminal status to
another, such as `Complete` to `Superseded`, does not, and neither
does an edit that leaves the status alone.

A revision in which the row cannot be read as terminal, for any
reason, reads as "not terminal" there, and the clock starts after it.
That covers a live status, but equally a status outside the shared
vocabulary and a table reshaped so that the row no longer parses. This
is the conservative direction, because it delays a retirement rather
than causing one, and it means an index-wide reformat resets every
row's clock.

A row is identified by its plan file's name, so renaming the file and
its index link together is a row the earlier revisions do not have,
and its clock starts at the rename. That is the direction to err in:
a rename delays a retirement rather than causing one, and the row
becomes eligible again on its own once the rename is older than the
threshold.

The history is walked newest first and the walk stops at the first
revision older than the threshold, so it costs what the index churned
inside the 90 days rather than what the repository has done since it
was created. A row still terminal in a revision older than the
threshold has been terminal at least that long, which is all
eligibility turns on, so an age is reported as a lower bound -- "at
least 97 days" -- and never as an unlabelled number.

The date read from a commit is the committer date, not the author
date: a rebase or a cherry-pick keeps the author date of the original
work, and what is being measured is how long the row has said terminal
in *this* repository.

The checkout the audit reads must carry that history. A shallow one
does not, so the criterion refuses to answer rather than report that
nothing is eligible; the audit workflow clones with
`--filter=blob:none` for this reason. A filtered clone fetches a
historical blob at the moment the criterion reads it, and the clone
leaves no credential behind, so that fetch is anonymous: the criterion
can only read the history of a repository the audit can read
anonymously.

There are three ways the criterion reports `error` rather than a
verdict: a shallow checkout, a revision of the index larger than the
2 MB cap a file is read under, and a walk that exceeds its five-minute
budget. Each is a statement about the audit rather than a finding about
the repository, which is why it files no issue and closes none.

### Rows that are reported rather than counted

Four kinds of row are named in the result and left out of the count,
because each is a plan this criterion cannot speak for, and silence
about them would read as approval:

* **A row already retired**, whose link is a permalink into this
  repository pinning a commit under `docs/plans/`. Retirement leaves
  the row and its status in place -- retired is not a status -- so the
  permalink is what says the work is done.
* **A row naming a plan somewhere else**, by any other absolute link.
  This repository's history cannot date it and this repository's pull
  request cannot retire it.
* **A row whose `Plan` cell cannot be read**: it links nothing, or its
  status is not one of the seven terms in the shared vocabulary. What
  the vocabulary is, and whose finding a status outside it is, belongs
  to [plan-index](/components/development/audits/plan-index/).
* **A terminal row whose status change the history does not show**,
  which is what an index created or renamed into place inside the last
  90 days looks like. Dating such a row by the commit that moved the
  file would retire a plan on the strength of a rename, so it is left
  undated and reported instead. It becomes eligible on its own once
  that commit is older than the threshold.

A row whose linked plan file is no longer in the tree is *not* one
of these: it is counted like any other. The criterion reads the
index and its history and never the plan's files, and what
retirement leaves behind is the row -- a terminal row pointing at a
plan that has already been deleted still has to become a permalink
before a reader can tell which it is. The `retire-plans` helper
reports such a row as unreadable instead, because its own job is to
move the plan's reasoning and it has no text to move; the two tools
disagree about the row on purpose, each answering for the part of
the work it does.

### A table with no `Status` column

A plan listed in a table that has no `Status` column records no
status, so it can never become eligible. The criterion reports those
plans and never fails for them, on a passing result as much as on a
failing one. The `findings` list carries every such plan, while the
`details` string carries a bounded summary of the same list, so a
passing result has `findings` on purpose.

That is deliberate, and it is the rule most likely to be argued with,
because it leaves a repository able to hold finished plans out of
retirement indefinitely by not giving them a status. The reason to
take it anyway is that the alternative is an audit demanding a status
somebody will invent to clear an issue, and an invented status in the
one page people read to decide what wants attention is worse than
none. A criterion cannot tell a deliberate omission from an oversight.
A repository keeping such a table should say in the index why, as
private-ci does, and `docs/plan-retirement.md` carries the rule so
that it is written down rather than implied by a check.

### What the issue means

The issue this files is a backlog, not a deadline. Nothing about a
plan goes wrong on the ninety-first day; the issue is the record of
which plans have reasoning that is no longer being maintained, and of
how much of `docs/plans/` is describing software that has since been
rewritten.

It also stays open while any eligible plan remains. A repository that
retires three of its eleven has made its index better and is not
finished, and the issue naming the other eight is the only record of
what is left. That is intended rather than an impatience: retirement
is deliberately one pull request per repository and one commit per
plan, reviewed and merged by a person, so a partial pass through a
long list is the expected shape of the work.

## Template

No template of its own: a plan is not written differently because it
will one day be retired, and the convention applies ninety days after
its last work lands, when nobody is reading the template.

The fix is the `retire-plans` skill, in the private `llm-setup`
repository. It lists the eligible plans, moves each one's reasoning
into the documentation that will be maintained, files the deferred work
still worth doing as issues labelled `from-retired-plan`, rewrites
every pointer to the plan, turns its index row's link into a permalink
and deletes its files -- one commit per plan, one pull request per
repository. The parts with one right answer are its `retire-plans`
helper; deciding where a plan's reasoning belongs is not one of them,
which is why retirement is never automatic.

A plan whose reasoning has no sensible home in `docs/` is not retired.
It is left where it is and reported, so that a person can decide:
deleting the only copy of a decision's justification to meet a 90-day
rule is exactly the loss retirement exists to prevent. Such a plan
keeps this criterion failing, which is the correct state -- something
is outstanding -- and the reason it is outstanding belongs in the
issue.

## Projects

Per-project compliance for this criterion is regenerated
on every run of the consistency audit: see
[the compliance page](/components/development/audits/compliance/#plan-retirement).
