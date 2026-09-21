# Phase 3: `docs/use-cases/standalone.md`

Master plan: [PLAN-use-case-docs.md](/components/kerbside/plans/PLAN-use-case-docs/)

Planned at **high effort**. Phases 1 and 2 were both rated high
because the material already existed elsewhere and the work was
deciding what not to say. This phase inherits that hazard in its
sharpest form yet and adds one of its own. The sharpest form:
`docs/console-sources.md:217-298` does not merely document the
static driver's options, it already contains the *framing* — an
"Intended use-cases" list and a "Not intended for production use"
paragraph — which is precisely what decision 2 of
[PLAN-demo-install.md](/components/kerbside/plans/PLAN-demo-install/) assigns to this page.
The new hazard: the survey found that framing is factually wrong
about the driver's central behaviour, so this phase cannot simply
decide what to delegate. It has to correct the thing it delegates
to first.

Review effort: **medium**. The master plan sets no review effort
for any page; oVirt's equivalent was reviewed at medium, phases 1
and 2 followed, and this phase does the same.

## Situation

Three use-case pages exist and agree on a format.
`docs/use-cases/ovirt.md` landed 2026-08-10 as
[PLAN-two-tier-ci-phase-04-docs.md](/components/kerbside/plans/PLAN-two-tier-ci-phase-04-docs/)'s
deliverable and settles it; `docs/use-cases/shaken-fist.md`
followed 2026-09-18 as phase 1, merge commit `2f0e526`; and
`docs/use-cases/openstack.md` landed 2026-09-20 as phase 2, merge
commit `a7df5e5`. All three carry identical `##` headings, which
is what makes the format a convention rather than a coincidence.

This phase writes the fourth, for the standalone case: Kerbside
fronting a fixed list of SPICE targets with no control plane
behind it at all. It is the only permutation with no cloud, and
the only one whose worked example already exists, is already
tested in CI, and is already documented at length in two other
files.

## Mission

Write `docs/use-cases/standalone.md`: why you would run Kerbside
against a static source, how the driver's no-control-plane model
differs from the three platform pages, what it genuinely cannot
do, and where the mechanics already live — without restating the
demo recipe in `docs/installation.md` or the option reference in
`docs/console-sources.md`.

Along the way, correct the survey's load-bearing finding: two
places state that the static console list cannot be changed
without a restart, and the code has re-read it every sixty
seconds since before either sentence was written.

## Scope

In scope:

- `docs/use-cases/standalone.md`, new.
- The hot-reload correction in `docs/console-sources.md` and in
  `kerbside/sources/static.py`'s header comment.
- Wiring: the `docs/index.md` Use Cases row gains a link, the
  README's curated list gains a fourth per-deployment entry, and
  the three sibling pages' `## See also` sections name the new
  page.
- One GitHub issue for the silent-reload gap the survey found
  (see decision 5).

Out of scope, explicitly:

- **The demo recipe.** `docs/installation.md:160-319` owns the
  commands, in order, with their real output, per
  [PLAN-demo-install.md](/components/kerbside/plans/PLAN-demo-install/) decision 2 settled
  2026-08-22. This page links it. It does not restate one command.
- **`demo/README.md`.** It is the reference for the compose stack
  itself and stays that.
- **`docs/direct-qemu-harness.md`.** The CI harness is a testing
  document, not a deployment one.
- **The `docs/index.md` introduction.** Phase 5's, per phase 2's
  decision 7, so the introduction is edited once rather than five
  times.
- **Fixing the silent-reload gap in code.** A documentation phase
  does not change the maintenance loop. It is filed, not fixed.

## What the survey found

Checked against the tree at `c41f530` on 2026-09-20.

**1. The master plan's phase 3 row holds in every particular, and
nothing in it needed correcting at source.** That is unusual
enough to be worth stating. `kerbside/sources/static.py` exists
(133 lines). `docs/installation.md` carries "Try it: the demo
stack" at `:160`, delivered 2026-08-22 as
[PLAN-demo-install.md](/components/kerbside/plans/PLAN-demo-install/)'s phase 5.
That plan's decision 2 is settled and says exactly what the
master plan's row says it says, including the sentence this
phase's scope is built on: the use-case page "owns the framing —
why you would run a static source, how it works, what it cannot
do — and links to the installation demo for the mechanics rather
than restating them". The promised pointer is in place.

**2. The load-bearing finding: the static source hot-reloads, and
two documents say it does not.** `_parse_sources()` in
`kerbside/main.py:64` re-opens `config.SOURCES_PATH` on every
call; the maintenance loop calls it every sixty seconds
(`kerbside/main.py:348-353`); and the static driver is
constructed fresh from that pass's YAML dict —
`static_source.StaticSource(**source)` at `kerbside/main.py:174`
— so the `consoles:` list is re-read with it. Both directions
work: a new entry is discovered and audited "Discovered new
console" (`:200-205`), and an entry removed from the file falls
into the cleanup at `:242-260`, is deleted, and is audited
"Console no longer available", because a static source *is*
scraped and so is never covered by the retention branch that
protects OpenStack.

Against that:

| Where | What it says | Verdict |
|-------|--------------|---------|
| `docs/console-sources.md:231-233` | "kerbside must be restarted to pick up changes, there is no polling" | Wrong on both counts |
| `kerbside/sources/static.py:36-37` | "Hot-reload is not supported. Restart kerbside to pick up changes to the consoles list." | Wrong |
| `demo/sources.yaml:5-6` | "reads this list at startup and once per 60-second maintenance cycle" | Correct |
| `kerbside/main.py:188-194` | "a static source yields the operator configured SPICE password here and the maintenance loop runs every 60 seconds" | Correct |

So the codebase already knows, in two places, what its own
operator documentation denies in two others. The third claim in
that `console-sources.md` paragraph — "there is no liveness check
on the QEMU process behind the ticket" — is true, and is the one
that actually earns the "not intended for production use" verdict.

**3. There is a third existing owner the master plan's row does
not name.** `docs/installation.md:304-319` carries a "What the
demo is not" table of six rows, one of which is "A static source,
not a cloud". So the standalone page has four neighbours to
reconcile with, not the two the row anticipates:
`console-sources.md` (reference), `installation.md` (recipe *and*
a limitations table), `demo/README.md` (the stack), and
`direct-qemu-harness.md` (the harness).

**4. The index row already exists, already links the demo, and
already claims CI coverage.** `docs/index.md:163` reads
`| Standalone / static source | The static driver
(kerbside/sources/static.py) for labs, demos, and direct-qemu
style fleets. The compose demo is the worked example |
direct-qemu, smoke tier and nightly |`, and the CI claim checks
out against `docs/testing.md:75`
(`direct-qemu-functional.yml`, pull_request + merge_group +
nightly, smoke tier). A paragraph after the table adds that the
lane "runs the full daemon + API + MariaDB stack against a local
qemu SPICE server via the static source, so it is the end-to-end
exercise of the standalone scenario". The page links this row
rather than adding one, and must agree with both the row and that
paragraph or change them.

**5. Something distinctive, load-bearing, and in no use-case page
yet: the static type gates a command.** `kerbside demo token`
refuses to mint unless *every* configured source is `type:
static` (`kerbside/main.py:474-526`), naming the offending source
and its type in the refusal and pointing at #300. This is the
only place a source's type changes Kerbside's behaviour outside
discovery, and it is the reason the demo stack can hand an
evaluator a bearer token at all.

**6. The spine the page has been missing.** Backend host-subject
pinning is the one question all four pages answer differently.
oVirt learns the subject from discovery; Shaken Fist learns it
from the platform; OpenStack cannot have one, because Nova's
validation response carries no certificate subject; and the
static source is the only case where the *operator* writes it by
hand, as an optional per-console field
(`docs/console-sources.md:262` and
`kerbside/sources/static.py:56-59`).
`demo/sources.yaml:33-43` leaves it deliberately commented out
and says why. That contrast is what makes this page an argument
rather than a description of a test fixture.

**7. No `sources.yaml` field comparison covers `consoles`.** The
dirty-check at `kerbside/main.py:119-122` lists `type`, `url`,
`username`, `password`, `project_name`, `user_domain_id`,
`project_domain_id`, `deleted` and `ca_cert`. A changed
`consoles:` list is therefore applied but never logged as a
source configuration change. The per-console discovery and
cleanup lines do fire, so the change is not invisible, but
nothing says the *file* changed. See decision 5.

## Decisions

**1. The page is `docs/use-cases/standalone.md`, not
`static.md`.** The three existing pages are named for a platform.
There is no platform here, and `static` is the name of a driver —
an implementation detail that `console-sources.md` owns. The
index row's scenario column already reads "Standalone / static
source", leading with the deployment shape, and the page should
match the noun a reader arrives with. The cost is that the file
name and the `type:` value differ by a word; the brief tells the
author to name the driver in the first sentence so the search
lands.

**2. The page owns the argument; `console-sources.md` keeps the
reference — but the framing moves.** Decision 2 of the
demo-install plan assigns "why you would run a static source" and
"what it cannot do" to this page, and `console-sources.md:223-233`
currently holds both. Leaving them there and writing them again
here is the phase-1 failure repeated. Moving them wholesale
strips a reference page of the context that makes its option
tables usable.

So: `console-sources.md` keeps a *short* version — the two
intended use-cases as a sentence, and the liveness-check
limitation, which an operator reading the option table needs
without a second click — and loses the rest to this page. The
option and field tables, the example YAML, and the ryll
control-socket pairing all stay exactly where they are. The page
never restates a field name.

**3. The hot-reload correction happens in this phase, in both
places, and is stated positively.** It would be defensible to
file it and move on; a docs phase that starts editing code
comments is a phase that has lost its edges. Two things override
that. The claim is one of the three the page's own "what it
cannot do" section rests on, so writing the page honestly
requires settling it. And the wrong sentence sits in the exact
paragraph this phase is already rewriting under decision 2 —
correcting it costs a line, where filing it guarantees the new
page and the reference contradict each other until someone picks
the issue up.

`kerbside/sources/static.py:36-37` is corrected as well, because
it is the same sentence and it is where the next person to touch
the driver will read it. That is the whole code change: a comment.

**4. The reload behaviour is a feature, and the page says so.**
Sixty-second reload in both directions is the single most useful
thing about the static source for the lab and fleet cases the
master plan names, and it has been hidden by a false claim for
the driver's whole life. It belongs in the value proposition, not
buried in a limitations row — with the sixty-second cadence, both
directions, and the audit events named, so a reader can verify it
from the log rather than take the page's word.

**5. The silent-change gap is filed, not documented as a
limitation.** Survey finding 7 — that a changed `consoles:` list
produces no "Source configuration changed" line — is a real gap,
but it is a logging gap in the maintenance loop rather than a
property of the standalone deployment, and the per-console
discovery and cleanup lines mean nothing is actually lost. It
gets an issue. The page does not carry a limitations row for it,
because a row that says "the log line you would expect is not
emitted, though four others are" is noise in a table whose other
rows are about what Kerbside cannot do.

**6. The page states the `kerbside demo token` gate, in "User
interaction model".** It is the one behaviour the `static` type
gates, and an evaluator who follows the demo and then adds a real
source will hit the refusal without warning. It is one paragraph,
it names #300 as the underlying gap the way the command's own
error message does, and it is not a limitations row — the refusal
is the feature.

**7. "Status and limitations" leads with the liveness gap.** Of
the three limitations the old paragraph claimed, one is being
deleted as false (restart), one is really a consequence
(no polling — the inverse), and one is true and serious: nothing
checks that the SPICE server behind a ticket is alive, so
Kerbside will mint a `.vv` for a dead target and the client
discovers it by failing to connect. That is the honest reason the
static source is not for production, and it should not be the
third row.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | high | opus | none | Write `docs/use-cases/standalone.md`. **Read first, in this order:** `docs/use-cases/ovirt.md` in full (the format authority — the `##` sections are Value proposition, How it works, How to set it up, User interaction model, Status and limitations, See also, in that order; "How it works" opens with a mermaid `flowchart TD`; "Status and limitations" is a table of what is *not* proven, one row each, naming why); `docs/use-cases/openstack.md` (the most recent sibling, for tone and for how it delegates to the reference pages); `docs/console-sources.md:217-298` (what you must **not** duplicate — every option name, every field name, the example YAML and the ryll control-socket paragraph stay there); `docs/installation.md:160-319` (the demo recipe and its "What the demo is not" table — link, never restate); `kerbside/sources/static.py` in full; `kerbside/main.py:64-260` (the maintenance loop, where the reload behaviour lives); `demo/sources.yaml` (the best existing prose on this source, in comments). **The page must cover:** the value proposition per decision 4 and survey finding 6 — no control plane to stand up, sixty-second reload in both directions, and the operator-written `host_subject` that makes this the only source where pinning is a hand-written field; the no-discovery model in "How it works", with the mermaid diagram showing `sources.yaml` as the source of truth rather than a cloud API; setup that links `docs/installation.md#try-it-the-demo-stack` for the worked example instead of restating it; the `kerbside demo token` static-only gate in "User interaction model" per decision 6; and "Status and limitations" led by the liveness gap per decision 7. **The contrast that gives the page its spine** is survey finding 6 — four pages, four answers to backend host-subject pinning — and it belongs in the value proposition. **Constraints:** relative links from `docs/use-cases/` need `../`; mermaid is linted by `tools/mermaid-lint.sh`, so run it; wrap prose at the width `openstack.md` uses (64); name the `static` driver in the first sentence, since the file is named for the deployment and not the driver; do not touch any other file in this step. |
| 3b | medium | opus | none | Two corrections of the same false claim, and one deletion. **Verify it yourself before editing** rather than trusting this plan: read `kerbside/main.py:64` (`_parse_sources` re-opens `config.SOURCES_PATH`), `:348-353` (the sixty-second maintenance loop calls it), `:174` (`StaticSource(**source)` is constructed from that pass's YAML dict), `:200-205` (a new console is added and audited) and `:242-260` (a console absent from the list is removed and audited, because a static source is in `scraped_sources` and so misses the retention branch). (i) `docs/console-sources.md:231-233` — the sentence "The console list is static — kerbside must be restarted to pick up changes, there is no polling, and there is no liveness check on the QEMU process behind the ticket" is wrong in its first two clauses and right in its third. Rewrite the paragraph to keep "Not intended for production use" and the liveness gap as the reason, and drop the restart and polling claims. Per decision 2, this paragraph also *shrinks*: keep the two intended use-cases as one sentence and the liveness limitation, and let the new page carry the rest — do not leave a second copy of the framing behind. (ii) `kerbside/sources/static.py:36-37` — the header comment says "Hot-reload is not supported. Restart kerbside to pick up changes to the consoles list." Replace it with what the loop actually does, naming the sixty-second cadence and both directions. This is a comment change; do not touch the driver's code. Python style for this repo: single quotes, wrap at 80. Do not touch anything else in either file. |
| 3c | medium | sonnet | none | Mechanical wiring, no prose invention. (i) `docs/index.md:163` — the `| Standalone / static source | ... |` row of the Use Cases table: turn the bare scenario cell into a markdown link whose text stays `Standalone / static source` and whose target is the new page's path relative to `docs/`, mirroring exactly how the oVirt, Shaken Fist and OpenStack rows link theirs. Leave the description and CI columns alone, and leave the paragraph below the table alone unless 3a's page contradicts it — if it does, report that rather than editing both. (ii) `README.md:43-45` — the curated list has three use-case entries, all in the `Per-deployment guide:` form with an absolute `https://github.com/shakenfist/kerbside/blob/develop/...` URL; add a fourth for the standalone case in the same form. (iii) add the new page to `docs/console-sources.md`'s `## Related Documentation` list, and name it in the `## See also` section of **all three** of `docs/use-cases/ovirt.md`, `docs/use-cases/shaken-fist.md` and `docs/use-cases/openstack.md`, matching the sibling-link form those sections already use (a bare `filename.md`, no directory prefix). **Do not** add a row to any table, and do not touch `docs/index.md`'s introduction — that is phase 5's. |
| 3d | low | sonnet | none | Verification and filing, no edits except to fix what it finds. Run, from the repository root: `tools/mermaid-lint.sh`; `diff <(grep '^## ' docs/use-cases/ovirt.md) <(grep '^## ' docs/use-cases/standalone.md)` (must be empty); `grep -rln 'standalone\.md' docs/ README.md` (expect `docs/index.md`, `docs/console-sources.md`, all three sibling pages, `README.md`, and the plan files — note the `-l` and the escaped dot: a sibling link inside `docs/use-cases/` correctly carries no directory prefix, so a pattern including one can never match it); `grep -in 'restart' docs/console-sources.md kerbside/sources/static.py` (expect no hit claiming a restart is needed to pick up console changes); and for every relative link in the new page, resolve it from `docs/use-cases/` and confirm both the file and the anchor exist. Then `pre-commit run --all-files`, which includes `tox -e py3` and therefore `test_docs_links.py` and flake8 over the changed `static.py`. Finally, file a GitHub issue for survey finding 7: `kerbside/main.py:119-122`'s dirty-check field list does not include `consoles`, so an edited static console list is applied without any "Source configuration changed" log line, unlike every other field in a source entry — quote the list and name the per-console lines at `:200-205` and `:255-257` that do fire, so the issue is not read as "the change is silent". Report anything that fails; do not paper over a broken anchor by deleting the link. |

Steps run in order. 3a is the phase. 3b is separable in principle
but see decision 3. 3c and 3d exist so that 3a's and 3b's briefs
can both say "do not touch any other file", which is the cheapest
way to stop a writing step from wandering.

The gotcha phases 1 and 2 recorded still applies:
`kerbside/tests/unit/test_docs_links.py` resolves every relative
`.md` link and anchor in every tracked markdown file, plan files
included, against that file's own directory. A plan cannot
contain a literal markdown link to a page under `docs/use-cases/`
— it resolves against `docs/plans/` and fails `tox -e py3`. Every
reference to a use-case page in this plan is therefore a code
span, not a link. Phase 4 inherits this.

## Risks and mitigations

- **The page duplicates `console-sources.md#static-source`.** The
  phase-1 hazard in its sharpest form: eighty-two lines, and
  unlike the Shaken Fist case they include the framing this page
  is supposed to own. *Mitigation:* decision 2 draws the line
  explicitly and 3b enforces it from the other side by shrinking
  the reference paragraph in the same change, so there is no
  window in which both copies exist. 3d greps for field names.

- **The correction is wrong.** "It reloads every sixty seconds"
  is a stronger claim than the one it replaces, and the survey
  made it from reading, not from running. *Mitigation:* 3b's
  brief requires the implementer to re-verify the five code sites
  independently before editing, and names them. If the loop turns
  out to be conditional in a way the survey missed, the correct
  outcome is to report it and stop, not to soften the sentence.

- **The page becomes a second demo guide.** Everything an author
  wants to show is already written in `docs/installation.md`, and
  the temptation to paste three commands is strong. *Mitigation:*
  decision 2 of the demo-install plan settled this in August and
  3a's brief quotes the boundary; 3d has no grep for it, so the
  review is what catches a recipe creeping in — call it out
  explicitly in the pull request description.

- **The index row's paragraph and the new page disagree.** The
  paragraph under the Use Cases table already asserts what the
  `direct-qemu` lane proves about this scenario. *Mitigation:*
  3a's brief names it as required reading and 3c's brief forbids
  editing both sides silently — a contradiction is reported, not
  reconciled by an implementer mid-step.

- **Editing a code comment in a documentation phase invites
  scope creep.** *Mitigation:* 3b's brief is explicit that the
  driver's code is not to be touched, and the definition of done
  asserts the diff to `kerbside/` is comment-only.

## Definition of done

Each of these is falsifiable from the tree:

- [ ] `docs/use-cases/standalone.md` exists, and its `^##`
      headings match `docs/use-cases/ovirt.md`'s exactly, in
      order.
- [ ] Its "How it works" section contains a mermaid `flowchart
      TD`, and `tools/mermaid-lint.sh` exits zero.
- [ ] None of the option or field names from
      `docs/console-sources.md:236-262` (`consoles`, `uuid`,
      `name`, `hypervisor`, `hypervisor_ip`, `insecure_port`,
      `ticket`, `secure_port`) appears in the new page, except
      `host_subject`, which survey finding 6 puts in the value
      proposition as the contrast with the other three pages, and
      `ca_cert`, which review round 2 established is the third
      requirement for the backend leg and so cannot be omitted
      from the setup section for the same reason.
- [ ] The page states the sixty-second reload, in both
      directions, and names the two audit events.
- [ ] `grep -in 'restart' docs/console-sources.md
      kerbside/sources/static.py` returns no hit claiming a
      restart is needed to pick up console list changes.
- [ ] `git diff develop -- kerbside/` touches only comment lines
      in `kerbside/sources/static.py`.
- [ ] The page links `docs/installation.md#try-it-the-demo-stack`
      and contains no `docker compose` command.
- [ ] A limitations row names the absent liveness check, and it
      is the first row.
- [ ] The `kerbside demo token` static-only gate appears in
      "User interaction model", naming #300.
- [ ] `docs/index.md`'s Standalone row links the page, and the
      paragraph below that table does not contradict it.
- [ ] `grep -rln 'standalone\.md' docs/ README.md` lists
      `docs/index.md`, `docs/console-sources.md`, all three
      sibling pages under `docs/use-cases/`, and `README.md`.
- [ ] `pre-commit run --all-files` passes, which includes
      `tox -e py3` and therefore `test_docs_links.py`.
- [ ] An open GitHub issue exists for survey finding 7, filed by
      step 3d.

## Registration

Recorded in the master plan's Execution table and in
`docs/plans/index.md` by the close-out commit that opens this
phase, which also sets phase 2 to Complete against merge commit
`a7df5e5` and ticks phase 2's definition of done, whose items
were all met but left unticked when it landed.

## Back brief

Before 3a writes a word, the implementer confirms in one
paragraph:

1. Which sentences currently in `docs/console-sources.md:223-233`
   are moving to the new page, which are staying, and which are
   being deleted as false. Decision 2 draws the line; this
   confirms the implementer reads it the same way.
2. That the five code sites in 3b's brief were re-read and the
   sixty-second, both-directions claim holds — or exactly where
   it does not.

No gate on the writing itself. The page is cheap to redraft and
the format is settled by three siblings; the expensive mistake
here is the boundary, which is what the back brief checks.
