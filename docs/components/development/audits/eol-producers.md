# Audit: End-of-life runner labels offered

## What we check

A runner label is a promise. A workflow writes `runs-on:
[self-hosted, vm, debian-12, s]` and a runner appears, built from
whatever the CI conductor decided `debian-12` means. The repository
naming the label chose almost nothing: it inherited a decision made
once, somewhere else, for the whole fleet.

[eol-distro](/components/development/audits/eol-distro/) measures the consumer end of that
relationship -- whether a repository still *names* a release we have
retired. This criterion measures the other end: whether a repository
still *offers* one.

The two are separate criteria because they are separate defects with
separate owners. A consumer fixes its finding by editing a workflow
and its `.github/actionlint.yaml`. A producer fixes its finding by
deleting a table entry, which is the only change that actually stops
the label being served. Folding them together would give one issue
title to two fixes, and the title is the fleet-wide idempotency key
for filing and closing.

They share the table. The retired releases and their runner labels
are listed once, in `EOL_RELEASES` in
`scripts/audit/checks/distros.py`, and documented once in
[the retired list](/components/development/audits/eol-distro/#the-retired-list). Adding the next
release is still one entry and one row, and both criteria start
reporting it on the same run. A second copy of the banned list here
is exactly the defect `eol-distro` was written to avoid.

### Where we look

Two definitions, in [private-ci](https://github.com/shakenfist/private-ci):

| File | Definition | What it decides |
|------|------------|-----------------|
| `conductor/imagebuilder.py` | `IMAGE_BUILDS` | which images the nightly build bakes into the `ci-images` namespace |
| `conductor/provisioner.py` | `CI_IMAGES` | which labels the provisioner will boot a runner from |

Each is a list of entries with a `label` key, and the `label` is what
a workflow may ask for. They are named rather than discovered: a
producer is a deliberate thing, and a walk looking for any list of
dicts with a `label` key in it would read tables that have nothing
to do with runners. Adding a third producer is a line in
`PRODUCER_DEFINITIONS`.

Only the label is read. The entries also carry the upstream image
each label is built *from*, and that is not this criterion: reading
it would report `debian:12` as a finding against the one repository
whose job is to turn that image into something bootable.

**The definitions are parsed as Python, with `ast`, rather than
grepped.** This is the load-bearing decision in the check, and it is
not about tidiness. Both producer modules discuss retired labels in
their own comments: at the time of writing `imagebuilder.py` carries
"ubuntu-2004 was dropped 2026-07-11" directly above the entries that
replaced it, so a grep of a *clean* producer reports a label nothing
has offered for months. The same thing has already been designed
around once -- the plan that reworded the `STALE_LABEL_SECONDS`
comment in that module forbade it from quoting a retired label at all,
because a grep-shaped gate would otherwise have fired forever after.
A criterion that greps inherits that false positive. An abstract
syntax tree has no comments in it.

A definition that is present but not in the shape the parse
understands -- the assignment renamed, the list replaced by a
function call, the `label` key spelled something else -- is reported
as an `error` rather than as a verdict. So is a table only part of
which can be read: the parse is all or nothing, and one entry built
by a call, spread from another dict or labelled with a constant
rather than a string fails the whole definition rather than being
skipped. So, too, is a module that changes the table at import time
after the assignment that is read, directly through the table's own
name -- a `+=`, an `.append()`, a second assignment, a store into
one of its entries -- because the literal is then not the whole
table. A change made through another name is out of reach: an
alias, a loop variable over the entries, or `list.append()` called
on the table. So is anything inside a function. Following a table
through other names is data-flow analysis, and the two definitions
this reads are plain literals that nothing modifies afterwards. The audit's `error` status
files no issue and closes none, and the workflow fails the leg that
produced it. The alternatives are both worse: a `pass` reports a
producer nobody actually read as clean, which is the vacuous pass
this criterion exists to avoid, and a `fail` files an issue against
private-ci, under the bot's identity, for a bug in this repository.

### What this does not cover

* **Published guest images.** `images`' `build.sh` still builds
  `debian:12`, `debian-docker:12`, `debian-gnome:12` and
  `debian-xfce:12` by default, on purpose: Debian LTS covers
  bookworm until 2028, and the entry says so. A guest image somebody
  boots deliberately, knowing which release it is, is not the same
  defect as a runner label a workflow is handed by default. **The
  distinction this criterion encodes is who chooses.** Reading build
  lists would open by filing four findings against a documented
  decision.
* **Whether anything still consumes the label.** A producer entry
  with no consumers left is the easy deletion and a producer entry
  with sixteen is a migration, and this criterion cannot tell them
  apart. `eol-distro`'s findings across the fleet are what says
  which it is, and they are also how the consumers get told.
* **Runner fleets that advertise no release label at all.** This is
  the generalisation worth carrying forward rather than leaving in a
  closed issue. A static fleet advertising only `self-hosted` and
  `static` is *structurally invisible* to a label-based audit: there
  is no label to match, so no criterion keyed on one can see what
  release those runners boot, and both this criterion and
  `eol-distro` report nothing about them however old they get. The
  fleet has one such set of runners today, in another organisation
  and out of reach of this tooling. Any future fleet of that shape
  needs the same treatment -- which is to say, a different
  instrument: something that reads what a runner *is* rather than
  what it is called.

### No exception marker

[eol-distro](/components/development/audits/eol-distro/) lets a line say `audit-ok: eol-distro`
with a reason, because its findings land on repositories whose
subject matter genuinely is old distributions -- test input built on
a retired release, an upstream job name, a guest image booted on
purpose. Those are all consumer shapes.

A producer has none of them. It is the one place the label can
actually be removed, so a decision to keep serving a retired release
is a decision about what the whole fleet may ask for. That belongs in
the retired list -- which is the registry of what may be asked for --
rather than in a comment on one of two files in one repository. If
such a decision is ever made, it is made there, in the open, and both
criteria stop reporting the label together.

## Template

No template -- this is a property of two definitions in one
repository, not a file to install.

## Projects

Per-project compliance is regenerated on every run of the consistency
audit: see [the compliance page](/components/development/audits/compliance/#eol-producers).
