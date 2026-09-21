# Phase 2: `docs/use-cases/openstack.md`

Master plan: [PLAN-use-case-docs.md](/components/kerbside/plans/PLAN-use-case-docs/)

Planned at **high effort**. Phase 1 was rated high because the
material already existed elsewhere and the work was deciding what
not to say. This phase inherits that hazard in a milder form and
adds three of its own: OpenStack is the first permutation that is
not a scraped source, so the page cannot borrow either existing
page's shape; the deployment story is genuinely in flight upstream
and must be written so that it does not rot; and phase 1's page
carries two uncorrected factual errors, one of which this page
would directly contradict.

Review effort: **medium**. The master plan sets no review effort
for any page; oVirt's equivalent was reviewed at medium, phase 1
followed, and this phase does the same.

## Situation

Two use-case pages exist. `docs/use-cases/ovirt.md` landed
2026-08-10 as `PLAN-two-tier-ci-phase-04-docs.md`'s deliverable
and settles the format; `docs/use-cases/shaken-fist.md` landed
2026-09-18 as phase 1 of this plan, merge commit `2f0e526`, and
confirms it. Both have identical section headings, which is what
makes the format a convention rather than a coincidence.

This phase writes the third, for OpenStack. It is the permutation
with the largest existing footprint in the documentation —
`docs/index.md:106-131` carries a full prose section on it — and
the only one whose deployment path is still moving upstream.

## Mission

Write `docs/use-cases/openstack.md`: what Kerbside is worth in a
Nova spice-direct deployment, how the push model differs from the
two scraped sources already documented, how to set it up given
that the Kolla-Ansible deployment change has not yet merged, and
what is honestly not proven yet.

Along the way, correct the two factual errors the PR #443 review
found in phase 1's page. Neither was auto-filed as an issue, so
nothing else tracks them.

## Scope

In scope:

- `docs/use-cases/openstack.md`, new.
- Two corrections to `docs/use-cases/shaken-fist.md` (survey
  finding 7), per decision 4.
- One row added to the OpenStack option table in
  `docs/console-sources.md`, per decision 6.
- Inbound links from the four places phase 1 used, plus the
  sibling link from `shaken-fist.md`.
- Filing an issue for the code defect in survey finding 6.

Out of scope, each with a reason:

- **The `docs/index.md` introduction slim-down.** Phase 5's job,
  per decision 7.
- **Fixing the `KeyError` in survey finding 6.** This is a
  documentation plan; the fix is a code change with its own test.
  File it, do not do it.
- **Any change to the `openstack_matrix` lane.** The page
  describes what the lane proves; it does not extend it.
- **Anything about Proxmox, aggregation or placement.** Phases 3
  and 4.

## What the survey found

Checked against `develop` at `dcd62d0` on 2026-09-19. The master
plan's phase row reads, in full: *"Nova 2025.1 spice-direct;
Kolla-Ansible deployment via kerbside-patches; much of
docs/index.md's OpenStack section moves here"*. All three clauses
survive, but the third is a phase boundary question rather than a
fact, and is settled by decision 7.

**1. There is no `kerbside/sources/openstack.py`.** The `sources/`
directory holds `base.py`, `ovirt.py`, `shakenfist.py` and
`static.py` only. `kerbside/main.py:139-142` matches
`type == 'openstack'` in the scrape loop and does
`skipped_sources.add(source['source']); continue`. Every piece of
OpenStack handling lives inline in `kerbside/api.py:536-663`
(`class NovaToken`), routed at `api.py:887` as `/nova-console.vv`.
Consoles are created at exchange time by the `db.add_console()`
call at `api.py:630`, not by discovery. This is the finding that
shapes the page: it cannot follow either existing page's
scrape-then-exchange narrative.

**2. `docs/console-sources.md`'s OpenStack section is accurate.**
Worth stating explicitly, because phase 1's central hazard was an
accurate eighty-line section it risked duplicating. Here the
equivalent is `docs/console-sources.md:182-214` — thirty-three
lines, correctly describing the on-demand model at `:29-38` and
documenting `type: openstack` with eight options whose names match
what `api.py` and `main.py:104-129` actually read. The `url` key
is the Keystone endpoint and is spelled `url`, not `auth_url`, in
both the table and the code. One gap only, finding 5.

**3. OpenStack consoles are never subject-pinned on the backend
leg.** `api.py:630-636` calls `db.add_console()` with `uuid`,
`source`, `hypervisor`, `insecure_port` and `secure_port` and
nothing else, so `host_subject` takes its default of `None`
(`kerbside/db.py:214-216`). Nova's `validate_console_auth_token`
response carries no certificate subject, so there is nothing to
populate it with. The `host-subject` that does appear in the
generated `.vv` at `api.py:646` is `config.PROXY_HOST_SUBJECT` —
the proxy's *own* subject, which pins the client-to-Kerbside leg,
a different leg entirely. Both other sources populate backend
`host_subject` from the platform. This is the sharpest difference
between this deployment and the two already documented, and
decision 3 puts it in the value proposition rather than only in
the limitations table.

**4. The upstream deployment state, queried today.** The Gerrit
REST API for `topic:spice-direct-consoles` returns:
`openstack/kolla` change 975495, *Implement container image build
for kerbside*, **merged**; `openstack/kolla-ansible` change
976889, *Deploy Kerbside with Kolla-Ansible*, **open**; and open
alongside it 988189 (CI scenario jobs), 988913 and 989614 (the two
tempest changes) and 967801 (routable SPICE console IP). Two
related kolla-ansible changes have merged: 967800, *Add additional
SPICE configuration options*, and 967802, *Allow requiring secure
channels with SPICE*. So `docs/index.md:127-131`'s claim — image
build merged, deployment code not — is still true, which was the
one claim in that section at risk of having gone stale. Decision 5
covers how the page records it.

**5. `verify` is an undocumented source option.**
`kerbside/api.py:581-591` reads `source.get('verify')`, accepting
a bool or the strings `'true'`/`'false'` case-insensitively and
defaulting to `True`, and passes it to the Keystone session. It
does not appear in the option table at
`docs/console-sources.md:197-207`, which documents `ca_cert`
instead. Two TLS knobs on the same source, one documented.

**6. A latent `KeyError` in the same block.**
`kerbside/api.py:595` formats its error message with
`source["name"]`, but `sources.yaml` entries are keyed `source` —
per the documented table, per `main.py`, and per every other read
in `api.py`. A source whose `verify` is neither a bool nor a
string therefore raises `KeyError: 'name'` instead of logging the
configuration error it was about to report. Out of scope to fix;
step 2d files it.

**7. Two uncorrected errors in phase 1's page.** PR #443's second
review round raised both, classified both `document`, and — unlike
round 1 — no issues were auto-filed for them, so nothing tracks
them:

- `docs/use-cases/shaken-fist.md:19-27` says the Shaken Fist path
  has *"no portal to write and no ticket plumbing to build, which
  is not true of the oVirt or OpenStack paths"*. True of oVirt.
  False of OpenStack, which embeds the broker in Nova exactly as
  Shaken Fist embeds it in its own API, and whose exchange
  Kerbside serves itself at `/nova-console.vv`. The real
  distinctions are that Shaken Fist's verification is offline
  where Nova's is a callback, and that its client library does
  mint-and-exchange in one call.
- `docs/use-cases/shaken-fist.md:48`, `:81` and `:128-136` present
  the backend leg as unconditionally TLS with CA verification and
  subject pinning. `rust/kerbside-proxy/src/backend.rs:78-110`
  dials `target.insecure_port` with `tls_port: None` and retries
  with TLS only `if is_need_secured(&first_err) && target
  .secure_port != 0`. Where a node's qemu does not demand TLS the
  leg stays plaintext and neither verification nor pinning
  happens. `ovirt.md:68` and `:98-104` get this right.

**8. The index scaffolding already exists.** `docs/index.md:158`
carries an OpenStack row in the Use Cases table, with the CI lane
already named. As in phase 1, the page phase links an existing row
rather than adding one.

**9. The lane is real and merge-tier.** `openstack_matrix`
(`.github/workflows/functional-tests.yml:861`) builds an all-in-one
Kolla deployment from `kerbside-patches` on a Debian 13 Shaken Fist
guest and runs a curated Tempest subset; `docs/testing.md:485-530`
is the authority, including why the guest distro is load-bearing.
It runs in the merge queue only, never per-PR — which is a real
limitation for this page to state, since it means OpenStack
regressions are caught later than Shaken Fist or static ones.

Nothing was corrected at source during planning this time: the
master plan's phase row and the `index.md` description both hold
as written. Phase 1 corrected four claims; this phase corrects
none, which is itself worth one sentence.

## Decisions

**1. The page follows `ovirt.md`'s section structure exactly.**
Both existing pages already do. A third that diverged would turn a
convention back into a coincidence, and phase 5 has to slim the
index against a predictable shape.

**2. "How it works" is built around push, not scrape.** The
diagram and the prose start from a user asking Nova for a console
and end at the relayed session; they do not open with discovery,
because there is none. The page says plainly that there is no
OpenStack source driver and that `type: openstack` entries are
skipped by the scrape loop, because an operator who has read
`console-sources.md` will otherwise reasonably expect the
discovery interval to apply.

**3. The backend-pinning gap goes in the value proposition, not
only the limitations table.** This is the decision most likely to
be argued with: it writes a weakness into the part of the page
that is closest to a sales pitch, and the obvious alternative is a
limitations row where it would still be technically disclosed. It
goes up front anyway, for three reasons. It is the single largest
difference between this deployment and the two already written, so
an operator comparing pages needs it where they are comparing. It
is not a defect to be embarrassed about but a consequence of what
Nova's API returns, which is worth explaining once, properly. And
burying exactly this kind of qualification is how phase 1 shipped
an incorrect security claim — the page said "the backend leg is
pinned" because the qualification had been pushed out of the
narrative. The mitigation is stated alongside: the client-facing
leg is still pinned via `PROXY_HOST_SUBJECT`, and the backend leg
is still TLS-escalated on demand and firewall-inspected.

**4. Phase 1's two errors are corrected in this phase.** Not a
separate PR, and not left to the auto-filing, which did not
happen. One of the two is the sentence this page would have to
contradict, so correcting it is a precondition for writing the
page honestly rather than a courtesy. The other is a security
misstatement live on `develop` right now. The cost is that this
phase's diff touches a file it did not create, which is worth
paying once.

**5. Upstream state is recorded as change numbers and a checked
date.** `docs/index.md:127` currently says *"At the time of last
update to this document..."*, which no reader can falsify and
which has no expiry. The page names change 976889 and says what
its status was on a stated date, so a reader can open it and check
in one click. The same treatment for the merged image-build change
975495.

**6. `verify` is documented in `console-sources.md` as part of
this phase.** One row in an existing table. The page delegates all
source configuration to that table rather than restating it —
which is the phase-1 lesson applied — and delegating to an
incomplete table is the same failure as duplicating a complete
one, just harder to see.

**7. The `docs/index.md` introduction slim-down stays in phase
5.** The master plan's page table says *"much of docs/index.md's
OpenStack section moves here"*, which reads like phase 2 work.
Phase 5 exists to do the index slim-down once, after every page
that could absorb material exists; doing OpenStack's share now
leaves the index half-slimmed for three phases and means editing
the same introduction twice. This page is written to *supersede*
`docs/index.md:106-131` — covering everything it covers, better —
so that phase 5's job is a deletion and a link, not a rewrite.

**8. The `KeyError` is filed, not fixed.** Per the skill's rule
that a plan records a defect it finds rather than scope-creeping
into fixing it, and per phase 1's precedent with #444.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | none | Write `docs/use-cases/openstack.md`. **Read first, in this order:** `docs/use-cases/ovirt.md` in full (the format authority — sections are Value proposition, How it works, How to set it up with platform-side / Network / Kerbside side / A worked example subsections, User interaction model, Status and limitations, See also; "How it works" opens with a mermaid `flowchart TD`, and "Status and limitations" is a table of what is *not* proven, one row each, naming why); `docs/use-cases/shaken-fist.md` (the sibling, for tone and for how it delegates to the reference pages); `docs/console-sources.md:182-214` (what you must **not** duplicate); `kerbside/api.py:536-663`, the `NovaToken` class, which is the entire OpenStack implementation; `kerbside/main.py:139-142`; `docs/testing.md:485-530`. **The page must cover:** the value proposition against Nova's own console story (spice-direct returns a URL pointing at Kerbside, so Kerbside is the console endpoint rather than a bolt-on, and OpenStack deliberately does not expose hypervisor ports to client networks); the push model — no source driver, no scrape, consoles created at exchange time at `api.py:630` — per decision 2; the `/nova-console.vv` exchange at a level that links to `console-sources.md#openstack` for the option table rather than restating it; the backend-pinning gap per decision 3, in the value proposition *and* as a limitations row; the merge-tier-only lane per survey finding 9, as a limitations row; setup split into OpenStack side, network, and Kerbside side, with the upstream state per decision 5. **Re-query the upstream state yourself** rather than trusting this plan's snapshot — `curl -s 'https://review.opendev.org/changes/?q=topic:spice-direct-consoles' \| tail -c +6 \| python3 -m json.tool` — and use the date you ran it. **Constraints:** relative links from `docs/use-cases/` need `../`; mermaid is linted by `tools/mermaid-lint.sh`, so run it; wrap prose at the width `ovirt.md` uses; do not touch any other file in this step. |
| 2b | medium | opus | none | Correct two passages in `docs/use-cases/shaken-fist.md`, both from survey finding 7, and **verify each against the code before editing** rather than applying the review's suggestion on trust. (i) The "no portal to write and no ticket plumbing to build" contrast at `:19-27` is false for OpenStack. Read `kerbside/api.py:536-663` and confirm that Kerbside serves the Nova exchange itself, then scope the contrast to oVirt and the scraped sources and replace the OpenStack half with the distinction that does hold: Nova also embeds the broker, but its Kerbside-side validation is a callback to Nova's `/os-console-auth-tokens/` API rather than an offline signature check. (ii) The backend leg at `:48`, `:81` and `:128-136` is presented as unconditionally TLS with CA verification and subject pinning. Read `rust/kerbside-proxy/src/backend.rs:78-110` and confirm the insecure-first dial and the `is_need_secured` escalation, then mirror how `docs/use-cases/ovirt.md:68` and `:98-104` phrase it: the diagram edge names the escalation, the prose paragraph spells it out, and the value-proposition bullet names the condition. Note that the page's own Network subsection already requires both ports to be reachable, which only makes sense given insecure-first dialing — the page currently contradicts itself, and after this step it should not. Do not touch anything else in that file. |
| 2c | medium | sonnet | none | Two mechanical edits, no prose invention. (i) `docs/console-sources.md` — add a `verify` row to the OpenStack option table at `:197-207`, between `password` and `project_name` or wherever the existing ordering puts it, describing it as: optional, accepts a boolean or the strings `true`/`false`, defaults to true, and controls TLS certificate verification for the Keystone session. Read `kerbside/api.py:581-591` to confirm the semantics before writing the row. Do not touch the rest of that file except as item (iv) below requires. (ii) `docs/index.md:158` — the `| OpenStack | ... |` row of the Use Cases table: turn the bare scenario cell into a markdown link whose text stays `OpenStack` and whose target is the new page's path relative to `docs/`, mirroring exactly how the oVirt and Shaken Fist rows link theirs. (This plan describes the link rather than showing it; see the note under this table.) Leave the description and CI columns alone. (iii) `README.md` — the curated list has two use-case entries, both in the `Per-deployment guide:` form; add a third for OpenStack in the same form, using the absolute `https://github.com/shakenfist/kerbside/blob/develop/docs/use-cases/openstack.md` URL that every link in that list uses. (iv) add the new page to `docs/console-sources.md`'s `## Related Documentation` list, and name it in the `## See also` section of **both** `docs/use-cases/ovirt.md` and `docs/use-cases/shaken-fist.md`. **Do not** add a row to any table, and do not touch `docs/index.md`'s introduction — that is phase 5's, per decision 7. |
| 2d | low | sonnet | none | Verification and filing, no edits except to fix what it finds. Run, from the repository root: `tools/mermaid-lint.sh`; `grep -rln 'openstack\.md' docs/ README.md` (expect `docs/index.md`, `docs/console-sources.md`, `docs/use-cases/ovirt.md`, `docs/use-cases/shaken-fist.md`, `README.md`, and the plan files — note the `-l` and the escaped dot, which matter: a sibling link inside `docs/use-cases/` correctly carries no directory prefix, so a pattern including one can never match it); `grep -n 'NEED_SECURED\|need_secured' docs/use-cases/shaken-fist.md` (expect at least one hit after 2b); `grep -n 'no portal to write' docs/use-cases/shaken-fist.md` (expect either no hit, or a hit scoped to oVirt alone); and for every relative link in the new page, resolve it from `docs/use-cases/` and confirm both the file and the anchor exist. Then `pre-commit run --all-files`. Finally, file a GitHub issue for survey finding 6 — `kerbside/api.py:595` formats an error with `source["name"]` where `sources.yaml` entries are keyed `source`, so a non-bool, non-string `verify` raises `KeyError` instead of logging the configuration error — quoting the line and naming the documented key. Report anything that fails; do not paper over a broken anchor by deleting the link. |

Steps run in order. 2a is the phase; 2b is separable and could in
principle be its own change, but see decision 4. 2c and 2d exist
so that 2a's and 2b's briefs can both say "do not touch any other
file", which is the cheapest way to stop a writing step from
wandering.

The gotcha phase 1 recorded still applies and applies to this file
too: `kerbside/tests/unit/test_docs_links.py` resolves every
relative `.md` link in every tracked markdown file, plan files
included, against that file's own directory. A plan cannot contain
a literal markdown link to a page under `docs/use-cases/` — it
resolves against `docs/plans/` and fails `tox -e py3`. Every
reference to a use-case page in this plan is therefore a code
span, not a link. Phases 3 and 4 inherit this.

## Risks and mitigations

- **The page duplicates `console-sources.md#openstack`.** The
  phase-1 hazard, milder — thirty-three lines rather than eighty —
  but the same failure. *Mitigation:* decision 2 fixes what the
  page owns (the push narrative and its consequences) against what
  it delegates (the option table and the API mechanics); step 2d's
  reviewer checks the option names do not appear in the new page
  at all.
- **Correcting phase 1's page reopens reviewed text and invites a
  review round on work already merged.** *Mitigation:* the
  corrections are the reviewer's own suggestions, and step 2b
  verifies each against `backend.rs` and `api.py` before applying
  rather than trusting them — which is what turned two `document`
  items into confirmed defects in the first place.
- **The upstream Gerrit state changes between this plan and the
  page.** Entirely likely; 976889 could merge next week.
  *Mitigation:* step 2a re-queries the REST API and dates its
  answer, and decision 5's change-number form means a stale
  sentence is one click from being caught rather than
  unfalsifiable.
- **Writing the pinning gap into the value proposition reads as
  undermining the product.** *Mitigation:* decision 3 requires the
  mitigation stated alongside the gap; the review checks that the
  paragraph explains *why* Nova cannot supply a subject, rather
  than leaving it as an unexplained absence.
- **The phase-1 rule bites again.** Forbidding duplication caused
  phase 1 to assert the *negation* of a mechanism it was not
  allowed to describe. *Mitigation:* carried into the definition
  of done below, unchanged.

## Definition of done

Each of these is falsifiable from the tree:

- [x] `docs/use-cases/openstack.md` exists, and its `^##`
      headings match `docs/use-cases/ovirt.md`'s exactly, in
      order.
- [x] Its "How it works" section contains a mermaid `flowchart
      TD`, and `tools/mermaid-lint.sh` exits zero.
- [x] None of the eight option names from
      `docs/console-sources.md:197-207` (`source`, `type`, `url`,
      `username`, `password`, `project_name`, `user_domain_id`,
      `project_domain_id`) appears in the new page. It links to
      the table instead.
- [x] The page states that there is no OpenStack source driver and
      that `type: openstack` entries are skipped by the scrape
      loop.
- [x] The absent backend `host_subject` appears both in the value
      proposition and as a row in the Status and limitations
      table, and the page distinguishes it from
      `PROXY_HOST_SUBJECT`, which pins a different leg.
- [x] A limitations row names `openstack_matrix` as merge-tier
      only, and says what that means for when regressions surface.
- [x] The page names at least one Gerrit change number with a
      status and the date it was checked.
- [x] `grep -n 'no portal to write' docs/use-cases/shaken-fist.md`
      returns nothing, or returns a sentence scoped to oVirt
      alone.
- [x] `grep -in 'need.secured' docs/use-cases/shaken-fist.md`
      returns at least one hit, and the page no longer says the
      backend leg is unconditionally pinned.
- [x] `verify` appears in the OpenStack option table in
      `docs/console-sources.md`.
- [x] `grep -rln 'openstack\.md' docs/ README.md` lists
      `docs/index.md`, `docs/console-sources.md`,
      `docs/use-cases/ovirt.md`, `docs/use-cases/shaken-fist.md`
      and `README.md`.
- [x] `pre-commit run --all-files` passes, which includes
      `tox -e py3` and therefore `test_docs_links.py`.
- [x] An open GitHub issue exists for survey finding 6: #451,
      filed by step 2d. Phase 1's two review findings were never
      auto-filed, which is why this phase had to carry them, so
      the issue number is recorded here rather than left to the
      filing.
- [x] **The rule forbidding duplication has not been read as
      licence to deny.** No sentence in either page asserts the
      *absence* of a mechanism merely because this plan forbade
      describing it. Forbidding a description is not permission to
      claim the thing does not happen. (Carried from phase 1,
      where exactly this produced a false security claim.)

## Registration

Done in the planning commit, not deferred:

- `PLAN-use-case-docs.md`'s Execution table: phase 1 moves to
  `Complete` with merge commit `2f0e526` recorded, and phase 2
  links this file and moves to `In progress`.
- `docs/plans/index.md`: the plan's row gains phase 1's outcome
  and this phase's description, reflecting what the survey found.
- There is still no `docs/plans/order.yml` in this repository to
  update.

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.

One gate, cheap to raise now and expensive to undo later:

- **Before step 2a writes any prose**, state the section outline
  and describe the diagram's nodes and edges in one paragraph.
  Decision 2 says the narrative runs from the user's request
  rather than from discovery, and that shape is the thing a reader
  either follows or does not. Agreeing it before the prose exists
  is much cheaper than restructuring a finished page, and this is
  the first page in the set that cannot copy an existing shape.

Steps 2b, 2c and 2d need no gate: 2b is two passages whose target
state is specified above, and 2c and 2d are mechanical.
