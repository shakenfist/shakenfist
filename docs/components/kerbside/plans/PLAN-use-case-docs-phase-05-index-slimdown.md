# Phase 5: the index slim-down and closeout

Master plan: [PLAN-use-case-docs.md](/components/kerbside/plans/PLAN-use-case-docs/)

Planned at **medium effort**, and this is the first phase in the
plan not planned at high. Phases 1 to 4 were rated high because
each had to decide what a new page said and what it deliberately
did not, against material scattered through reference docs. This
phase writes one paragraph of new prose. Everything else is
deletion, link rewiring, and one factual correction — and the
hard decision, what `docs/index.md`'s OpenStack section is
allowed to keep, was taken in phase 2 rather than here (decision
7 of [PLAN-use-case-docs-phase-02-openstack.md](/components/kerbside/plans/PLAN-use-case-docs-phase-02-openstack/),
which wrote `openstack.md` to *supersede* the index section so
that this phase's job would be "a deletion and a link, not a
rewrite"). Reporting that as medium rather than inheriting high
from the phases before it is the honest rating.

Review effort: **medium**, following oVirt and phases 1 to 4.
The master plan sets no review effort for any page.

## Situation

All six writable use-case pages exist and agree on a format.
`ovirt.md` landed 2026-08-10 as
[PLAN-two-tier-ci-phase-04-docs.md](/components/kerbside/plans/PLAN-two-tier-ci-phase-04-docs/)'s
deliverable; `shakenfist.md` 2026-09-18 as phase 1 (`2f0e526`);
`openstack.md` 2026-09-20 as phase 2 (`a7df5e5`);
`standalone.md` 2026-09-21 as phase 3 (`28efa6c`); and
`multi-cloud.md` with `placement.md` 2026-09-22 as phase 4
(`8c5c042`). Proxmox stays deferred and is not a phase.

What is left is the work the Mission describes and every phase
so far has explicitly declined to touch: `docs/index.md`'s
introduction still carries the OpenStack implementation story
and the Bumblebee comparison it had before any use-case page
existed. Phase 1 declined it because the destination page did
not exist yet; phases 2, 3 and 4 each declined it by name and
pointed here. Alongside it sits the `README.md` growth that
phase 1's risk table predicted and recorded for this phase: one
curated bullet per use-case page, six of them now, against a
policy that says README is a pitch with curated links rather
than a catalogue.

## Mission

Slim `docs/index.md`'s introduction to the generic broker model,
with the platform-specific material deleted or moved to the page
written to receive it; collapse `README.md`'s six use-case
bullets to one link at the index's Use Cases section; and bring
the two project reference files that index `docs/use-cases/` up
to date with a directory that is no longer "oVirt today".

## Scope

In:

- `docs/index.md`'s introduction: `### Implementation in
  OpenStack` (`:106-133`) and `### What About Bumblebee?`
  (`:134-145`), plus the two places in the surviving prose that
  state the OpenStack broker role wrongly (`:53` and the diagram
  node at `:90`).
- `docs/use-cases/openstack.md`: receives the Bumblebee
  comparison and the Nova specification link.
- `README.md`: the six use-case bullets at `:43-48` collapse to
  one.
- `ARCHITECTURE.md`: the two places that describe
  `docs/use-cases/` as holding the oVirt page (`:444`, `:477`).
- Closing out phase 4 in the master plan's Execution table and
  `docs/plans/index.md` — done in this plan's first commit, per
  the `plan-phase-landing` shared block.

Out:

- **The numbered broker model at `:42-84` and the connection
  flow diagram at `:86-104`.** These *are* the generic broker
  model the Mission says the introduction slims down to. They
  stay, and the only edit either takes is the factual correction
  named above. A phase that also rewrote them would be doing a
  different job.
- **Phase 6, the push audit.** It runs over the accumulated diff
  of phases 1 to 5 and closes itself out in its own pull
  request, per the shared block. Nothing here pre-empts it, and
  this phase does not mark the master plan complete.
- **The substance of any use-case page.** `openstack.md` gains a
  bullet and a link; no page's claims change. The cross-page
  consistency sweep that would otherwise have been closeout work
  was pulled forward into phase 4 (its decision 3) and is done.
- **Proxmox**, which has no source driver. `kerbside/sources/`
  still holds `base.py`, `ovirt.py`, `shakenfist.py` and
  `static.py` and nothing else, rechecked 2026-09-23.
- **Any code change.** Defects found while surveying are filed,
  not fixed.

## What the survey found

Four findings, one of which is a factual error rather than a
staleness. The master plan's phase 5 description held in every
particular and needed no correction at source; its Status
section did, and this plan's registration commit fixes it.

### 1. The index still says OpenStack has no broker

`docs/index.md:52-54`, inside the numbered broker model:

> In the OpenStack case this role is likely performed by Horizon
> or Skyline, although this is not yet implemented.

`docs/use-cases/openstack.md:365-373` says the opposite, and
says it as the section's lead:

> **Nova itself.** The intended path, and the reason this
> deployment needs no portal written for it. The user asks Nova
> for a console for an instance; Nova decides whether they may
> have it, using Keystone and its own project model, and mints
> the token.

The page is right. `kerbside/api.py:926` registers `NovaToken`
at `/nova-console.vv`, and the console URL Nova hands the user
is built from its own `[spice] spice_direct_proxy_base_url`
pointing at that endpoint — there is no portal in the path and
none is waiting to be written. The diagram node at `:90` carries
the same error in four characters: `External Broker<br/>
(SF/Horizon)`.

This matters more than the two sections being moved, because it
is a *surviving copy* rather than a duplicate. Phase 1's review
caught this exact claim on the Shaken Fist page — recorded in
`docs/plans/index.md` as "it claimed OpenStack has a portal to
write, where Nova embeds the broker exactly as Shaken Fist
does" — and corrected it there. Nobody looked at the index,
because phases 1 to 4 had all put the index introduction out of
scope. Correcting it is this phase's job and it is the one edit
here that changes what a reader believes.

### 2. The OpenStack section is superseded, not duplicated

Every claim in `docs/index.md:106-133` is on `openstack.md`, and
in each case the page states it more precisely:

| Index claim | Where `openstack.md` says it |
|---|---|
| Nova 2025.1 Epoxy added `spice-direct` | `:10`, and `:195` as a version prerequisite |
| Nova returns a URL pointing at Kerbside with a token | `:14-18`, naming `spice_direct_proxy_base_url` and `/nova-console.vv` |
| Kerbside validates via `/os-console-auth-tokens/` | `:89`, in the flow diagram |
| Hypervisors must not be reachable from the client network | `:41-45`, as a value-proposition bullet |
| `kerbside-patches` is the sample deployment | `:230-256` and the See also list |
| Kolla merged the image build, Kolla-Ansible has not merged the deployment | `:236-241`, a table of four changes with their states, dated 2026-09-20 at `:409` |

The last row is the reason this is a deletion rather than a
merge: the index narrates the upstream state in prose that has
to be reworded every time a change merges, and the page replaced
it with a table of change numbers. Keeping both means keeping
the one that rots. This is exactly what phase 2 decision 7
committed to.

One asset in the index section is not on the page: the
[Nova specification](https://specs.openstack.org/openstack/nova-specs/specs/2025.1/implemented/libvirt-spice-direct-consoles.html)
link at `:110`. It also appears at
`docs/console-sources.md:214`, so deleting the index section
does not lose it from the tree — but `openstack.md` is where a
reader of that page would look for it, and it has no spec link
at all. Step 5a adds it.

### 3. Bumblebee appears nowhere else in the tree

`grep -rn -i bumblebee --include='*.md' .` outside `docs/plans/`
returns `docs/index.md:134-145` and nothing more. So unlike
finding 2, this section cannot simply be deleted: it is the
tree's only copy, and it is a genuine piece of positioning — it
answers "how does this relate to the other thing in this space?"
for a reader who has heard of Bumblebee.

It is also OpenStack material. NeCTAR is an OpenStack research
cloud, Bumblebee orchestrates OpenStack instances, and the
comparison's whole point is that Bumblebee occupies the *broker*
role in Kerbside's model. `openstack.md` already has a section
that enumerates brokers — `## User interaction model` at `:357`,
with bullets for Nova itself, Kerbside's own web UI, and Nova's
HTML5 console. Bumblebee is a fourth entry in that list, and the
list is already making the same argument the Bumblebee section
makes, one bullet further down: the HTML5 path "bypasses
Kerbside entirely and is not a session Kerbside can see, audit
or terminate."

### 4. The two reference files still say "oVirt today"

`ARCHITECTURE.md:444` annotates the directory in its tree
listing as `# Per-deployment operator guides (oVirt today)`, and
`:477` links `docs/use-cases/ovirt.md` under the label "Use
Cases - Per-deployment operator guides" — a link to one page
standing in for six. `AGENTS.md:24` does it correctly already,
routing "How is it deployed against a specific cloud?" to the
directory rather than to a page, and needs no change.

`.claude/CLAUDE.md`'s Documentation section says "oVirt today;
the remaining pages are tracked in
`docs/plans/PLAN-use-case-docs.md`", which is the same
staleness in a file that is loaded into every session. It is in
scope for the same reason and by the same one-line edit.

### 5. Nothing else was stale

The Use Cases table's six linked rows each agree with their
page's opening claim; the `Tested in Kerbside CI` column agrees
with `docs/testing.md`'s lane inventory; and the paragraph under
the table ("Scenarios without a link are planned rather than
written") is still true, because the Proxmox row is still
unlinked. `docs/console-sources.md`'s Related Documentation list
names the three source pages it should and correctly omits the
two topology pages, which document no source. No change needed
to any of them.

## Decisions

**1. `### Implementation in OpenStack` is deleted, not moved.**
Finding 2 shows every claim already has a better home. The
deletion is the whole edit; the Use Cases table two screens
below already links the page, so no replacement pointer is
needed or wanted. Adding a "see the OpenStack page" stub would
reintroduce the thing the table exists to do.

**2. Bumblebee moves to `openstack.md`'s User interaction
model, as a fourth bullet.** This is the decision most likely to
be argued with, and the alternative is real: `docs/index.md` has
a `## Related Projects` section (`:337`) that already positions
ryll against Kerbside, and Bumblebee is arguably the same kind
of content. I am choosing against it for two reasons. Related
Projects is about *our* projects a Kerbside operator might also
run — it is one entry, ryll, and it exists to tell an operator
about a tool they can use. Bumblebee is not that: it is a
comparison with something a reader might mistake Kerbside for,
and the answer is a classification ("Bumblebee is a Broker in
our model"), which only means anything next to the enumeration
of brokers. That enumeration is now on the OpenStack page. The
cost of being wrong is one bullet in the wrong file, and the
back brief gates on this.

**3. The comparison is re-cut as a bullet, not transplanted as
a section.** The existing text is four sentences of prose
wrapped at 80 columns with a `###` heading. `openstack.md` wraps
at 64 and its user-interaction bullets are bolded-lead
paragraphs. Pasting the section in would be visible as a graft.
The brief for 5a therefore asks for the *claim* to be carried
across — Bumblebee is a broker, it orchestrates HTML5 via
Guacamole, so it misses SPICE's richer features and carries
HTML5's performance cost — in the destination's voice and
width, keeping the two links.

**4. `README.md` collapses to one entry.** Phase 1's risk table
recorded this for the closeout in as many words: "six more
bullets is the wrong end state ... the closeout phase should
collapse them to a single link to the Use Cases index section."
The six per-deployment and topology bullets at `:43-48` become
one, pointing at `docs/index.md#use-cases`. The other curated
links — installation, configuration, console sources, proxy
architecture, schema, SPICE, development, testing — are
untouched: they are not a catalogue of one growing family, they
are eight different documents.

A second, smaller argument for it: `test_docs_links.py` skips
any target containing `://` (`:83`), so every absolute
`blob/develop/...` URL in README is unchecked by CI. Collapsing
six unchecked links to one is a small reduction in the surface
that can rot silently. It is not a fix for that gap and this
phase does not attempt one.

**5. The numbered broker model stays, and takes one edit.** Per
scope. The Horizon/Skyline clause at `:53` is replaced with what
Nova actually does, and the diagram node at `:90` changes from
`SF/Horizon` to `SF/Nova`. Both are inside the section this
phase otherwise leaves alone, which is why they are called out
here rather than left to an implementer's judgement.

**6. `.claude/CLAUDE.md` and `ARCHITECTURE.md` are in scope;
`AGENTS.md` is not.** The first two make a claim about
`docs/use-cases/` that is false. The third already routes to the
directory and says nothing about its contents, which is what the
doc-discipline policy wants a summary-and-index file to do.

**7. Nothing found here is fixed in code.** The survey found no
code defect, so unlike phases 1 to 4 this phase files no issue.
If one turns up during implementation it is filed and named in
the back brief, not fixed.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | medium | opus | none | The only prose-invention step, and it runs **first** so the destination exists before anything is deleted. Two edits to `docs/use-cases/openstack.md`, no other file. (i) Add a fourth bullet to `## User interaction model` (`:357-388`), after the `**Nova's HTML5 console**` bullet, covering Bumblebee. Read `docs/index.md:134-145` for the claim to carry: [Bumblebee VDI](https://github.com/NeCTAR-RC/bumblebee) from the NeCTAR research cloud is superficially similar to Kerbside but is a *Broker* in Kerbside's model — it orchestrates the creation of and access to virtual desktops — and it exclusively orchestrates HTML5 consoles through Apache Guacamole, so it misses SPICE's richer features and carries an HTML5 desktop's performance cost. **Write it in the destination's voice, not the source's**: the surrounding bullets open with a bolded lead (`**Nova itself.**`) and then a short paragraph; match that, keep both links (the Bumblebee repository and Guacamole if you want it), and wrap at 64 columns like the rest of the file. Do not open with "What about Bumblebee?" or carry the heading across. (ii) Add the Nova specification link — `https://specs.openstack.org/openstack/nova-specs/specs/2025.1/implemented/libvirt-spice-direct-consoles.html` — to the `## See also` list at `:415`, as its own entry describing it as the spec that added the `spice-direct` console type. The list's entries are `- [Title](url) — description` with an em dash; match it, and put the new entry before the `kerbside-patches` one so that entry stays last (phase 4's review established that ordering). **Constraints:** do not change any existing claim on the page; do not touch `docs/index.md` (5b deletes from it); run `tools/check-backend-tls-claims.py` afterwards, because `docs/use-cases/*.md` is in its scope. |
| 5b | medium | sonnet | none | `docs/index.md` only, four edits, no prose invention beyond one clause. (i) Delete `### Implementation in OpenStack` entirely, `:106-133` — heading and all four paragraphs. Add nothing in its place; the Use Cases table at `:148` already links the page. (ii) Delete `### What About Bumblebee?` entirely, `:134-145`. Step 5a has already put its content on `docs/use-cases/openstack.md`; confirm that before deleting, by grepping that file for `Bumblebee`, and stop and report if it is not there. (iii) `:52-54`, inside numbered step 1 of the broker list, currently reads "In the OpenStack case this role is likely performed by Horizon or Skyline, although this is not yet implemented." That is false — Nova performs the broker role itself. Replace the clause with an accurate one: Nova itself is the broker in the OpenStack case, minting a console token and returning a URL that points at Kerbside. Keep it to roughly the length of what it replaces, keep the sentence's place in the numbered item, and wrap at the file's width (80 columns; check the neighbours). Read `docs/use-cases/openstack.md:357-373` for the accurate statement before writing it. (iv) `:90`, the mermaid node `broker["External Broker<br/>(SF/Horizon)"]` — change `Horizon` to `Nova`, nothing else. **Constraints:** do not touch the Use Cases table, the numbered list apart from item (iii), the diagram apart from item (iv), or anything below `## Documentation Index`. Run `tools/mermaid-lint.sh` (the diagram is linted in CI) and `tools/check-backend-tls-claims.py` (`docs/index.md` is in its scope). |
| 5c | low | sonnet | none | Two files, mechanical. (i) `README.md:43-48` — the curated Documentation list currently has six use-case entries: `Kerbside for oVirt`, `Kerbside for Shaken Fist`, `Kerbside for OpenStack`, `Kerbside standalone`, `Multi-cloud aggregation`, `Placement topologies`. Replace all six with a single entry in the same `- [Title](absolute-url) - description` form the list uses, titled `Use Cases` (or `Deployment guides`), pointing at `https://github.com/shakenfist/kerbside/blob/develop/docs/index.md#use-cases`, and describing it as one page per deployment permutation — oVirt, Shaken Fist, OpenStack, standalone, plus the multi-cloud and placement topologies — each covering the value proposition, how it works and how to set it up. Keep it on one line like its neighbours. Leave the `Documentation Index` entry at `:42` and everything from `:49` down alone. (ii) `ARCHITECTURE.md` — at `:444` the directory tree annotates `use-cases/` as `# Per-deployment operator guides (oVirt today)`; there are six pages now, so drop the parenthetical or replace it with something that will not rot (`# Per-deployment and topology guides`). At `:477` the Documentation list links `docs/use-cases/ovirt.md` under the label `Use Cases`; point it at `docs/index.md#use-cases` instead, so one page stops standing in for six. Leave `:260`'s oVirt row alone — that link is correctly page-specific. **Do not** touch `AGENTS.md`, which already routes to the directory. |
| 5d | low | sonnet | none | `.claude/CLAUDE.md`, one edit. Its `## Documentation` section says per-deployment operator guides live in `docs/use-cases/` "(oVirt today; the remaining pages are tracked in `docs/plans/PLAN-use-case-docs.md`)". Six pages exist — `ovirt.md`, `shakenfist.md`, `openstack.md`, `standalone.md`, `multi-cloud.md`, `placement.md` — and only Proxmox is outstanding. Reword the parenthetical to say so without listing all six (this file is loaded into every session, so it costs context on every task): name that the six exist and that Proxmox stays deferred until a source driver does, keeping the plan link. Touch nothing else in the file. |
| 5e | low | sonnet | none | Verification and reporting, no edits except to fix what it finds. Run, from the repository root: `tools/mermaid-lint.sh`; `tools/check-backend-tls-claims.py` (expect exit 0 over 7 files); `grep -rn -i bumblebee --include='*.md' .` (expect `docs/use-cases/openstack.md` and the plan files, and **not** `docs/index.md`); `grep -rn -i 'horizon\|skyline' --include='*.md' docs/ README.md ARCHITECTURE.md AGENTS.md` (expect no hit claiming either is the OpenStack broker); `grep -c 'use-cases/' README.md` (expect 0 — the collapse leaves no per-page link); and confirm by hand that `docs/index.md` still has a heading whose GitHub slug is `use-cases`, since `README.md` and `ARCHITECTURE.md` now both depend on that anchor and `test_docs_links.py:82` skips absolute URLs so CI cannot catch it. Then `pre-commit run --all-files`, which includes `tox -e py3` and therefore `test_docs_links.py` and `test_check_backend_tls_claims.py`. Report anything that fails; do not paper over a broken anchor by deleting the link. |

Steps 5a and 5b must run in that order and cannot be
parallelised: 5b deletes the only copy of what 5a is moving, and
its own brief makes it check for the destination first. 5c, 5d
and 5e are independent of each other but 5e must run last.

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Deleting `### Implementation in OpenStack` loses a claim that turns out not to be on `openstack.md` after all. | Finding 2 is a claim-by-claim table with line numbers on both sides, built by reading both documents rather than by trusting phase 2's intent. Checked at review, not by the implementer: re-read the deleted block against the six destinations. The one asset genuinely missing, the spec link, is step 5a item (ii). |
| Bumblebee ends up in the wrong file, and a reader looking for "how does this compare to X?" does not find it on a page about deploying against Nova. | Decision 2 states the reasoning and the alternative, and the back brief gates on it before 5a writes anything. If the back brief disagrees, the fix is to put it in `docs/index.md`'s `## Related Projects` instead, which is a one-paragraph change to the same step, not a replan. |
| The README collapse breaks the `#use-cases` anchor silently, because `test_docs_links.py` cannot check an absolute URL. | Named in decision 4 and checked explicitly in 5e, by hand, against the actual heading in `docs/index.md`. Two files now depend on that anchor rather than one, which is why the check is a step rather than a hope. |
| The phase edits `docs/index.md`, which phase 4's plan flagged as the file phase 4 and phase 5 collide on. | Phase 4 has merged (`8c5c042`) and this worktree branches from `f63bd40`, after it. The collision was about the Use Cases table versus the introduction prose, and this phase touches only the introduction — which is the split phase 4 recorded. |
| The correction at `docs/index.md:53` restates Nova's behaviour and gets it slightly wrong in a new way, which is what happened to a rewritten sentence in phase 4 round 2. | The brief names the file and lines to read for the accurate statement (`openstack.md:357-373`) rather than describing the behaviour in the brief and inviting a paraphrase of a paraphrase. Reviewer checks the new clause against `kerbside/api.py:926` and the page, not against this plan. |

## Definition of done

Falsifiable items. Each is a command or a check against the
tree, not a claim about effort.

- [ ] `grep -n 'Implementation in OpenStack' docs/index.md` and
      `grep -n 'What About Bumblebee' docs/index.md` both return
      nothing.
- [ ] `grep -rln -i bumblebee --include='*.md' . | grep -v
      '^./docs/plans/'` returns exactly
      `./docs/use-cases/openstack.md`.
- [ ] No file under `docs/`, nor `README.md`, `ARCHITECTURE.md`
      or `AGENTS.md`, says that the OpenStack broker role is
      performed by Horizon or Skyline, or that it is not yet
      implemented. Check with a case-insensitive grep for
      `horizon` and `skyline` across all four; the surviving
      hits, if any, must not be about the broker role.
- [ ] `docs/index.md`'s mermaid broker node names Nova, and
      `tools/mermaid-lint.sh` passes.
- [ ] `grep -c 'use-cases/' README.md` is 0, and the curated
      Documentation list contains exactly one entry pointing at
      `docs/index.md#use-cases`.
- [ ] `docs/index.md` contains a heading whose GitHub anchor is
      `use-cases`, verified by hand rather than by the test
      suite, because `test_docs_links.py:82` skips every target
      containing `://` and both new links are absolute.
- [ ] Neither `ARCHITECTURE.md` nor `.claude/CLAUDE.md` claims
      `docs/use-cases/` holds only the oVirt page. `grep -n
      'oVirt today' ARCHITECTURE.md .claude/CLAUDE.md` returns
      nothing.
- [ ] `docs/use-cases/openstack.md`'s `## See also` links the
      Nova specification, and `kerbside-patches` is still its
      last entry.
- [ ] `diff <(grep '^## ' docs/use-cases/ovirt.md) <(grep '^## '
      docs/use-cases/openstack.md)` is still empty — the added
      bullet went inside an existing section and did not
      introduce a heading.
- [ ] `tools/check-backend-tls-claims.py` exits 0.
- [ ] `pre-commit run --all-files` is clean, including
      `test_docs_links.py`.
- [ ] Phase 4 reads `Complete` with merge commit `8c5c042` in
      both the master plan's Execution table and
      `docs/plans/index.md`, and this phase is registered in
      both. The master plan is **not** marked complete: phase 6
      remains.

## Registration

Done in this plan's first two commits, per the
`plan-phase-landing` shared block and the `next-phase` skill:

- Phase 4's row in the master plan Execution table moves to
  `Complete` with `8c5c042`, and its `docs/plans/index.md`
  fragment gains the same.
- The master plan's Status section is corrected at source. It
  says "The remaining three writable pages are unblocked" and
  describes three pages carrying identical headings; six exist
  now and all six carry them.
- This file is linked from the master plan's phase 5 row and
  from the `docs/plans/index.md` phase 5 fragment, with the
  description reflecting what the survey found rather than what
  the master plan predicted.
- `docs/plans/order.yml` is not touched; it registers master
  plans only.

## Back brief

Before executing any step, back brief the operator on the
understanding of this plan.

**Gate on the back brief before step 5a.** The step writes the
only new prose in the phase and 5b then deletes the source, so
getting the destination wrong costs two steps rather than one.
The back brief should state, in its own words: why the OpenStack
section is deleted outright while the Bumblebee section is
moved; which file Bumblebee lands in and which section of it,
and what the argument against `## Related Projects` is; and what
the broker model at `docs/index.md:42-84` is allowed to keep. If
any of those three comes back differently from the plan, resolve
it before any file is written.
