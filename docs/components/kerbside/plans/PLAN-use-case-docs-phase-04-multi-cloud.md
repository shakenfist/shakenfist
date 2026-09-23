# Phase 4: `multi-cloud.md` and `placement.md`

Master plan: [PLAN-use-case-docs.md](/components/kerbside/plans/PLAN-use-case-docs/)

Planned at **high effort**. The three page phases before this one
were rated high because the material already existed somewhere
else and the work was deciding what not to say. This phase
inverts that: one of its two pages has almost no material
anywhere, and the other has material scattered across three
pages that each state it slightly differently. Deciding what to
say and deciding where the single copy of it lives are both
judgement calls, and the second one edits documents that phases
1 to 3 already shipped.

Review effort: **medium**, following oVirt and phases 1 to 3.
The master plan sets no review effort for any page.

## Situation

Four use-case pages exist and agree on a format.
`docs/use-cases/ovirt.md` landed 2026-08-10 as
[PLAN-two-tier-ci-phase-04-docs.md](/components/kerbside/plans/PLAN-two-tier-ci-phase-04-docs/)'s
deliverable and settles it; `shakenfist.md` followed
2026-09-18 as phase 1, merge commit `2f0e526`; `openstack.md`
2026-09-20 as phase 2, merge commit `a7df5e5`; and
`standalone.md` 2026-09-21 as phase 3, merge commit `28efa6c`.
All four carry identical `##` headings.

Those four are *source* pages: each describes one driver, its
scrape or exchange flow, and its configuration. The two pages
this phase writes are not. They describe how Kerbside is
deployed rather than what it talks to, they add no driver and
no configuration key, and they are the two rows of the
`docs/index.md` Use Cases table that have never had a link.

They are also inverses of each other, which is why the master
plan groups them into one phase. Multi-cloud aggregation is many
sources behind one Kerbside. Placement topologies is many
Kerbsides in front of one cloud. They share a survey, they share
a set of facts about what the backend leg actually does, and
getting one right without the other would leave the pair
contradicting each other.

## Mission

Write `docs/use-cases/multi-cloud.md` and
`docs/use-cases/placement.md`, link both from the Use Cases
table, and make the whole `docs/use-cases/` directory state one
consistent set of facts about the Kerbside-to-hypervisor leg --
including on the four pages that already exist.

## Scope

In scope:

- `docs/use-cases/multi-cloud.md`, new.
- `docs/use-cases/placement.md`, new.
- The two `docs/index.md` Use Cases rows, which gain links.
- The three existing "One entry point across clouds" bullets,
  which are reconciled against the new multi-cloud page.
- Every sentence in `docs/use-cases/` that describes backend
  TLS or certificate pinning, which is swept for the
  overstatement survey finding 3 describes.
- The master plan's two proposed-page rows and the `index.md`
  description, corrected at source by the planning commit (see
  *Registration*).

Out of scope:

- Any code change. The survey found no bug this time; findings
  3 and 4 are documentation defects about correct code.
- `docs/index.md`'s introduction. The master plan assigns the
  slim-down to phase 5 and this phase does not pre-empt it. The
  boundary: phase 4 owns what the use-case pages say and
  whether they agree with each other; phase 5 owns the index
  prose that introduces them and the plan's closeout.
- `docs/console-sources.md`. Multi-source configuration is a
  list in `sources.yaml` with no new key, so the reference page
  needs nothing.
- A Proxmox page. Still no driver: `kerbside/sources/` holds
  `base.py`, `ovirt.py`, `shakenfist.py` and `static.py` and
  nothing else, rechecked 2026-09-22.
- Making either scenario CI-covered. Survey finding 5 says
  nothing exercises two sources today. Building a lane that
  does is a plan of its own, and the pages say "Not covered"
  because it is true.

## What the survey found

Seven findings. Three are stale claims in the master plan,
corrected at source by the planning commit; four are facts the
pages need and did not have written down anywhere.

### 1. The multi-cloud row understates what already exists

The master plan's proposed-page row says aggregation is
"no longer stated *nowhere*, but stated in one sentence -- the
`docs/index.md` Use Cases row, and one bullet of
`docs/use-cases/ovirt.md`". That was true when phase 1 was
planned. It is now the index row plus **three** bullets, one on
each cloud page: `ovirt.md:40`, `shakenfist.md:63` and
`openstack.md:65`.

The three are not equivalent. oVirt's and Shaken Fist's are
three lines each and say the same thing with the cloud names
rotated. OpenStack's is nine lines and carries real content
phase 2 discovered: more than one OpenStack cloud can be
configured at once, a presented token is offered to each in
turn in `sources.yaml` order until one validates it, every
configured cloud therefore sees tokens minted by the others,
and one broken cloud stops the exchange for the rest.

So the aggregation page is not writing on a blank sheet. It is
deciding which of those four places owns each fact.

### 2. Placement topologies is stated nowhere at all

Grepping every tracked markdown file for `regional office`,
`placement topolog`, `WAN`, `close to its users`, `per-office`,
`multiple kerbside`, `several kerbside` and `kerbside
instances`, and discarding hits under `docs/plans/`, leaves
exactly one: `docs/index.md:160`, the row itself. There is no
prose anywhere describing why you would run more than one
Kerbside.

This is the only genuinely greenfield page in the plan, and it
is the one most at risk of being invented rather than
documented. Every claim it makes has to come from the code.

### 3. The placement row overstates backend TLS

The master plan's placement row says SPICE over the WAN "is
exactly the kerbside-to-hypervisor backend leg: TLS'd (with
host-subject pinning), firewall-inspected, audited". The
parenthesis is wrong twice, and the code says so plainly.

`rust/kerbside-proxy/src/backend.rs:85-108` connects to the
insecure port first and escalates only inside
`if is_need_secured(&first_err) && target.secure_port != 0`.
TLS on the backend leg is the hypervisor's decision, signalled
by it rejecting plaintext, and it needs a secure port to
escalate to. `backend.rs:198-211` then maps an empty `ca_cert`
or an empty `host_subject` to `None`; the comment at `:206`
says so, and the CI-ORACLE comment at `:93-98` exists because
an empty subject "silently disabl[es] verification".

Pinning is therefore conditional on the source supplying a
subject, and one source never does. The only `db.add_console()`
call on the OpenStack path is `kerbside/api.py:665-671`, inside
`NovaToken`, and it passes `uuid`, `source`, `hypervisor`,
`insecure_port` and `secure_port` -- no `host_subject`, no
`ca_cert`. Phase 2 found this and put it in `openstack.md`; the
master plan's row was never updated to match.

**This is the fourth phase in a row to catch an overstated
backend-TLS claim.** Phase 1 asserted the leg was
unconditionally pinned. Phase 2's review found the trust-store
fallback inverted. Phase 3's review found the CA requirement
omitted from a setup procedure. Now the master plan's own row.
The pattern is not that the pages are careless; it is that the
truth lives in `backend.rs` and in ryll's connector, and every
phase has reconstructed it from the Python side and got a
plausible, wrong answer. Decision 4 responds to that directly.

### 4. Who tells the user which Kerbside to use is the real axis

The master plan's claim that per-office placement is natural for
scraped sources and needs routing for OpenStack holds, and the
reason is sharper than the row states.

Every `.vv` file Kerbside emits builds the client-facing address
from this Kerbside's own configuration: `config.PUBLIC_FQDN` at
`api.py:533` (direct), `:686` (Nova) and `:814` (Shaken Fist).
A Kerbside in a regional office therefore hands out its own
address without being told to. That part is free.

What differs is how the user reaches a Kerbside in the first
place:

- **Shaken Fist** verifies its console token offline, an
  Ed25519 signature check in `kerbside/sf_token.py`, and the
  route deliberately carries no `@verify_token` decorator
  (`api.py:707-711`). Any Kerbside holding the public key can
  serve any token, so an office Kerbside needs no coordination.
- **oVirt** and **static** users authenticate to a Kerbside
  directly and are scraping independently, so the same holds.
- **OpenStack** does not work this way. Nova is configured with
  one Kerbside URL by `kerbside-patches`, and the token it
  mints is presented to whichever Kerbside that URL names.
  Per-office placement needs something to route the user to
  their office's instance.

### 5. "Not covered" is verified, not assumed

Both index rows claim the scenario is not exercised in CI. That
is true, and each lane says so in its own words.
`tools/ovirt-e2e/gen-sources.py:4` emits "exactly one ``type:
ovirt`` source". `tools/sf-e2e/deploy-kerbside.sh:8` writes
"sources.yaml with one type: shakenfist source".
`tools/ovirt-e2e/deploy-kerbside.sh:15-16` writes "a
sources.yaml holding one type: ovirt source".
`tools/direct-qemu/lane-up.sh:48-53` is a heredoc with a single
static entry, and `demo/sources.yaml` has one static source
carrying one console.

No lane configures two sources, and nothing anywhere runs two
Kerbsides. The limitation is the first row of both pages'
Status and limitations tables, and it cites these files rather
than asserting itself.

### 6. OpenStack's multi-cloud claim is true

Checked because the aggregation page inherits it.
`api.py:612-645` iterates the parsed `sources.yaml` in file
order, skips anything whose `type` is not `openstack`, and
`continue`s past a `NotFoundException` or an empty validation
result. The first cloud that validates the token wins, and the
loop falls through to a 404 if none does. Offering order is
file order, exactly as `openstack.md:65` says.

### 7. Nothing else was stale

The master plan's `kerbside/sources/` inventory, its statement
that Proxmox has no driver, and its description of the index
scaffolding all still hold, rechecked 2026-09-22. The Use Cases
table has seven rows and the two this phase writes are the last
two without links, other than Proxmox.

## Decisions

**1. Two pages, not one.** The Execution table has a single row
for this phase, which invites a single page covering both. The
index has two rows, each expecting its own link, and the two
scenarios have different audiences: aggregation is read by
someone consolidating clouds, placement by someone with one
cloud and several offices. One page would serve neither well
and would leave one index row permanently linkless. The phase
stays one phase because the two share a survey and must agree
with each other.

**2. Both pages keep the six `##` headings.** Value
proposition, How it works, How to set it up, User interaction
model, Status and limitations, See also -- byte-identical to
`ovirt.md`, as the other three are. These are topology pages
rather than source pages, so some sections are genuinely
short: "How to set it up" for aggregation is a `sources.yaml`
with more than one entry in it, which is a paragraph, not a
procedure. The plan's instruction is to let a short section be
short rather than padding it to match the cloud pages. The
format is a convention across four pages and breaking it here
would cost more than the awkwardness of a three-line section.

**3. The multi-cloud page owns the token-ordering fact, and
`openstack.md` loses it.** The nine-line bullet at
`openstack.md:65` is the only place the offering-order and
shared-trust-domain consequences are written down. That is the
right fact in the wrong document: it is a property of running
several OpenStack clouds behind one Kerbside, which is the
aggregation page's subject. So the material moves, and
`openstack.md`'s bullet shrinks to the two sentences its
siblings carry plus a link.

The two three-line bullets on `ovirt.md` and `shakenfist.md`
stay as they are, with a link added. A reader on the oVirt page
should not have to leave it to learn that Kerbside can front
more than one cloud; they should have to leave it to learn what
that costs.

This edits two documents that phases 1 and 2 shipped, which is
deliberate and is the smaller half of decision 4.

**4. This phase sweeps backend-TLS claims across all six pages,
rather than leaving it to phase 5.** Survey finding 3 is the
fourth instance of the same error class in four phases. Three
of the four were caught by review rather than by writing, which
means the next one will be too. Step 4d therefore reads every
sentence in `docs/use-cases/` that describes the backend leg
and makes each of them name its condition, and the definition
of done carries a script that fails when an unconditional
pinning claim reappears.

This is the decision most likely to be argued with, on two
grounds, and both are fair.

The first is scope: phase 5 exists to do the index slim-down
and closeout, and a cross-page consistency sweep is closeout
work. Widening phase 4 to cover pages it did not write makes
its diff larger and its review longer, and if the sweep is
wrong it is wrong about three merged documents rather than two
new ones. The counter-argument is the one the repeated instances
make for themselves: phase 5 is one phase away, and every phase
so far has re-derived this fact and got it wrong. Deferring the
fix guarantees a fifth instance in the page this phase is about
to write, because the placement page's central claim *is* the
backend leg. The sweep is cheapest exactly here, where the facts
are already in hand and verified.

The second is method: a grep-based done-criterion is a blunt
instrument for a prose property, and it will produce false
positives on sentences that are already correct. That is
accepted. The script's job is not to prove the prose is right;
it is to make the next person who writes an unconditional
pinning sentence trip over something. Survey finding 3 exists
because nothing tripped.

**5. The placement page documents the routing gap as a
limitation, not as a recommendation.** Finding 4 establishes
that OpenStack per-office placement needs the user routed to
their local Kerbside, and Kerbside has no mechanism for that.
The page says so and stops. It does not propose DNS
geo-routing, a load balancer configuration, or anything else
Kerbside does not do and has never been tested with -- that is
how a use-case page turns into untested advice. If a reader
wants a recommendation, the honest one is "not solved here",
and it goes in the limitations table with the other things that
are not covered.

## Steps

All implementation is by sub-agents, per the execution model
shared block in `PLAN-TEMPLATE.md`. Step 4a is gated on the
back brief.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | high | opus | none | Write `docs/use-cases/multi-cloud.md`. Six `##` headings byte-identical to `docs/use-cases/ovirt.md` -- check with `diff <(grep '^## ' docs/use-cases/ovirt.md) <(grep '^## ' docs/use-cases/multi-cloud.md)`. Prose wraps at 64 columns, matching the other four pages. Subject: one Kerbside brokering several sources at once. Source the content from survey findings 1 and 6 in this plan, not from a fresh reading: the offering-order material moves here verbatim in substance from `docs/use-cases/openstack.md:65`, and `kerbside/api.py:612-645` is the code that backs it. The value proposition is that users keep one console entry point as workloads move between providers, and that all of it lands in one audit trail and one firewall policy. The limitations table's first row is that nothing in CI runs two sources at once -- cite `tools/ovirt-e2e/gen-sources.py:4`, `tools/sf-e2e/deploy-kerbside.sh:8` and `tools/direct-qemu/lane-up.sh:48`. The second is the shared trust domain: every configured OpenStack cloud sees tokens minted by the others and one broken cloud stops the exchange for the rest, so this suits clouds under one operator. "How to set it up" is a `sources.yaml` with more than one entry and a link to `docs/console-sources.md`; do not restate any option table. Do not invent a configuration key -- there is no new one. Commit subject: `docs: add the multi-cloud aggregation page.` |
| 4b | high | opus | none | Write `docs/use-cases/placement.md`, same six headings and same 64-column wrap. Subject: several Kerbsides placed by user population rather than by cloud, one per regional office, so the WAN hop is the Kerbside-to-hypervisor backend leg. **Read `rust/kerbside-proxy/src/backend.rs:85-115` and `:190-215` before writing a word about that leg**, and state the conditions survey finding 3 sets out: the proxy dials the insecure port first and escalates only when the hypervisor rejects plaintext with NEED_SECURED *and* a secure port is configured, and it pins the certificate subject only when the source supplied one -- an empty subject maps to `None` and disables verification silently. Do not write that the backend leg is TLS'd or pinned without naming the condition; that error has been made in each of the three previous phases. The rest of the page comes from survey finding 4: every `.vv` carries this Kerbside's own `PUBLIC_FQDN` (`kerbside/api.py:533`, `:686`, `:814`), so an office instance needs no special configuration to hand out its own address, and the axis that actually matters is who tells the user which Kerbside to use. Shaken Fist verifies its token offline (`kerbside/sf_token.py`, and `api.py:707-711` explains the absent decorator) so any instance serves any token; oVirt and static users authenticate directly; Nova is configured with one Kerbside URL by kerbside-patches, so per-office placement there needs routing that Kerbside does not provide. Per decision 5, that goes in the limitations table as a gap, with no proposed workaround. First limitation row is that no CI lane runs two Kerbsides. Commit subject: `docs: add the placement topologies page.` |
| 4c | medium | sonnet | none | Wire both pages in and reconcile the three existing aggregation bullets. In `docs/index.md`, link the `Multi-cloud aggregation` row (line 157) to `use-cases/multi-cloud.md` and the `Placement topologies` row (line 160) to `use-cases/placement.md`, matching the link style of the five linked rows. Leave both "Tested in Kerbside CI" cells reading `Not covered` -- survey finding 5 verified that is still true. Then, per decision 3: shorten the nine-line bullet at `docs/use-cases/openstack.md:65` to the two-sentence form its siblings carry at `ovirt.md:40` and `shakenfist.md:63`, and add a link to the new multi-cloud page; the material you remove must already be present on that page, so read it first and do not delete anything that did not survive the move. Add the same link to the `ovirt.md` and `shakenfist.md` bullets without otherwise changing them. Every page's 64-column wrap must survive: reflow any paragraph you touch. Commit subject: `docs: wire the two topology pages in.` |
| 4d | high | opus | none | The consistency sweep, decision 4. Read every sentence in `docs/use-cases/*.md` that describes the Kerbside-to-hypervisor leg, backend TLS, certificate pinning, `host_subject` or `ca_cert`, and make each one name its condition, using `rust/kerbside-proxy/src/backend.rs:85-115` and `:190-215` as the authority. The conditions are: TLS happens when the hypervisor rejects plaintext with NEED_SECURED and a secure port is configured; subject pinning happens when the source supplied a subject, and an empty one silently disables verification; a private CA must be supplied or the target is verified against the public web trust store and the handshake fails. Change only sentences that are unconditional or wrong -- this is a correction pass over merged documents, not a rewrite, and each hunk will be diffed individually. `openstack.md` already states that Nova never supplies a subject; do not duplicate that onto the other pages. This brief also told the sub-agent that `ovirt.md` documents pinning properly and was not to be weakened, which turned out to be wrong -- see the second finding below. Then add the check to the definition of done as a runnable script under `tools/`, per the falsifiable-verification habit. Commit subject: `docs: state the backend TLS conditions once.` |

## Risks and mitigations

**The placement page is invented rather than documented.**
Survey finding 2 found no prose anywhere about running several
Kerbsides, which means there is nothing to check a draft
against and every sentence is load-bearing. Mitigation: step
4b's brief names the code for every claim it is allowed to
make, and the management session checks each paragraph against
a file rather than against plausibility. Anything the sub-agent
adds beyond the brief gets deleted unless it can cite
something.

**The sweep in 4d breaks a page that was right.** It edits
three merged documents, and `ovirt.md` in particular already
documents pinning correctly. Mitigation: the brief says so
explicitly, and the management session diffs every hunk
individually rather than reading the summary. Any hunk that
changes a sentence which was already conditional is reverted.

**The offering-order material is lost in the move.** Decision 3
deletes seven lines from `openstack.md` on the strength of them
existing on a new page. Mitigation: 4c is briefed to read the
new page first, and the definition of done asserts the fact
appears exactly once in `docs/` rather than at least once.

**A fifth backend-TLS overstatement lands in the new pages.**
The pages this phase writes are more exposed to it than any
before, because the placement page's central claim is about
that leg. Mitigation: the explicit read-first instruction in
4b, the sweep in 4d, and a done-criterion that greps for the
claim shape across the whole directory including the two new
files.

**Phase 4 and phase 5 collide on `docs/index.md`.** Both touch
it. Mitigation: the scope section draws the line -- phase 4
changes two table cells and nothing else in that file; the
introduction prose is phase 5's.

## Definition of done

Falsifiable items. Each is a command or a check against the
tree, not a claim about effort.

- [ ] `docs/use-cases/multi-cloud.md` and
      `docs/use-cases/placement.md` both exist.
- [ ] For each of the two new pages,
      `diff <(grep '^## ' docs/use-cases/ovirt.md) <(grep '^## ' <page>)`
      is empty.
- [ ] No line in either new page exceeds 64 columns.
- [ ] `docs/index.md`'s `Multi-cloud aggregation` and
      `Placement topologies` rows each link their page, and
      both still read `Not covered` in the CI column.
- [ ] `tox -e py3` passes, including
      `kerbside/tests/unit/test_docs_links.py`, which resolves
      every relative link and anchor in every tracked markdown
      file. Both new pages land in the same commit as the rows
      that link them.
- [ ] The offering-order *mechanism* -- that a presented
      OpenStack token is tried against each configured cloud in
      `sources.yaml` order -- is stated in exactly one file
      under `docs/`, excluding `docs/plans/`. Check with
      `grep -rln '`sources.yaml` order' docs/ --include='*.md' |
      grep -v '^docs/plans/'`; the count is 1, not "at least 1".

      Scoped 2026-09-22, during implementation: the plan files
      quote the phrase because this very criterion contains it,
      and the master plan's proposed-page row records the move.
      A criterion that its own text violates is not checkable.
      Plans are a planning record, not documentation of the
      mechanism.

      Refined 2026-09-22, during implementation. The original
      criterion also grepped for `in turn`, which catches
      incidental uses that have nothing to do with this fact --
      `openstack.md:158` says libvirt TLS is required "which in
      turn requires", and `:412` counts Keystone calls "for each
      configured cloud in turn" while making a different point
      about rate limiting. A criterion that fires on those is
      not checkable, so it greps the mechanism's own phrase
      instead.

      The criterion constrains the mechanism, not the
      consequence. Another page may carry a limitation row
      naming what aggregation costs its own readers, and link
      out for how it works; a row that cannot drift because it
      does not restate the mechanism is not the duplication
      this item exists to prevent. `openstack.md` keeps exactly
      one such row, per the finding below.
- [ ] `tools/check-backend-tls-claims.py` exists, is
      executable, runs in a CI job that a documentation-only
      pull request does **not** skip, and exits non-zero when a
      sentence in `docs/use-cases/` or the Use Cases table in
      `docs/index.md` asserts backend TLS or certificate pinning
      without a conditional word near it. Proved by mutation,
      and the mutations are a committed tool rather than a
      session: `tools/mutate-backend-tls-claims.py` breaks the
      guard once per rule and asserts
      `kerbside/tests/unit/test_check_backend_tls_claims.py`
      catches each, restoring from a copy -- not with `git
      checkout`. The criterion is satisfied by every mutation
      being caught, not by their number, which is deliberately
      not written down here: an earlier draft said "nine ways"
      and was stale within a round.

      Amended 2026-09-23, after review. As first written this
      item said "wired into the lint job the way the other
      `tools/` checks are", and that was satisfied and wrong:
      `sanity_checks` is gated on `check_paths`, whose filter
      excludes `docs/**`, so the guard was invisible to every
      documentation-only pull request -- the entire class it
      exists for. The criterion now names the property rather
      than the placement.
- [ ] Neither new page names a configuration key or a database
      field, with one recorded exception. These are topology
      pages; `docs/console-sources.md` owns the option tables.
      Check with a grep for the field names the phase 3 plan
      enumerated, plus `sources.yaml` keys, allowing
      `sources.yaml` itself.

      The exception is `placement.md`, which is permitted
      `PUBLIC_FQDN`, `host_subject` and `ca_cert`. Narrowed
      2026-09-22, during implementation, rather than left to be
      broken silently. The item's purpose is to stop a topology
      page restating an option table, and these three are not
      that: two of them are the fields whose *absence* leaves the
      backend leg unpinned, which is the page's central claim,
      and the third is what makes a per-office `.vv` carry that
      office's address. Phase 3's review established the
      precedent -- a passage that describes this leg without
      naming the CA is how that phase's setup section came to be
      wrong. `multi-cloud.md` names none of them and keeps the
      unqualified form of the item.
- [ ] Every limitation row on both pages names why it is a
      limitation and cites a file, following the pattern
      `ovirt.md` established.
- [ ] `pre-commit run --all-files` is clean.

## Found during implementation

The survey found no bug, and said so. Step 4a's implementation
found one, in code the phase does not touch and will not change.

**Console rows are keyed on the identifier alone, not on the
pair of source and identifier.** `add_console()` looks the row
up with `filter(Console.uuid == uuid)` and no source
(`kerbside/db.py:308`), `get_console()` takes a `source`
argument and does not use it in its filter (`kerbside/db.py:391`),
and `remove_console()` deletes every row matching the identifier
(`kerbside/db.py:434`). The rest of the system disagrees: the
maintenance pass keys its bookkeeping on the pair
(`kerbside/main.py:88` and `:207`), and tokens and audit events
filter on source and identifier together
(`kerbside/db.py:350-352` and `:363-365`).

So two sources publishing one identifier share one row. The
later writer overwrites the address, the ports and the recorded
certificate subject, while the row keeps the source it was first
inserted under, because the update path never assigns `source`.
Either source removing its console deletes the row out from
under the other, whose next pass then re-adds it. The API
consequence is that `/console/<source>/<uuid>` serves the
console whatever source is named in the path.

This is aggregation-specific by construction: one source cannot
collide with itself, because duplicate identifiers within a
source are caught in the driver. It is distinct from
`standalone.md`'s existing "duplicate identifiers are tolerated"
row, which is about one source's own list.

Out of scope for this phase on the plan's own rule -- a
documentation phase records a bug and files it rather than
fixing it -- and the multi-cloud page documents the behaviour
with the citations above rather than waiting on a fix. Filed as
#468, which re-verified the six sites and found two things this
section had not.

The keying is a schema decision rather than a query habit:
`Console.uuid` is declared `primary_key=True` with `source` an
ordinary column, where `AuditEvent` in the same file makes both
columns primary. A fix therefore needs a migration, not four
edited filters. And the workaround at `kerbside/api.py:781` --
which asserts the row's source matches the token's, and whose
comment already names the keying as its reason -- does not
cover the overwrite direction, because the update path never
assigns `source`. The row still reads as the first source's
while pointing at the second's hypervisor, so the guard passes
and a single-use Shaken Fist console token is relayed to the
wrong hypervisor. The five other `get_console()` callers carry
no such check at all, `kerbside/rpc/servicer.py:110` among
them.

### ovirt.md overstated it too, which makes five

The plan asserted, and step 4d's brief repeated, that
`ovirt.md` is the page that documents this leg properly.
Narrowing the guard's vocabulary surfaced two sentences there
that do not, and the code agrees with the guard rather than
with the plan.

`kerbside/sources/ovirt.py:117` yields
`host_cache.get(vm.host.id)`, and that cache is only populated
under `if vm.host.id and ...` at `:99`. A VM the engine reports
as up with no host id therefore yields `host_subject: None`,
which `rust/kerbside-proxy/src/backend.rs:206` maps to `None`
and relays unpinned rather than erroring. `ovirt.md` stated
pinning unconditionally in its value proposition and again in
its step-by-step walk-through, and its limitations table --
which records single-host testing and live migration -- never
mentioned it.

Both sentences now name the condition, and both say plainly
that in an ordinary cluster every running VM has a host, so
this is the edge rather than the case. That distinction is why
the plan believed `ovirt.md` was correct: it is much closer to
right than the pages phases 1 and 3 wrote, and it is still not
right.

### What the guard actually catches

Weaker than this plan assumed when it specified the guard, and
the difference is worth recording because the done-criterion
reads as though the guard closes the hole.

It tests blocks -- a bullet, a paragraph, a table row -- and a
block naming any condition anywhere satisfies it. So it catches
a bald claim in a block of its own, which is the error phase 1
made. It does **not** catch a claim mixed into a block that
conditions something else, which is the error phase 3 made and
which step 4d found in `shakenfist.md`: that bullet correctly
conditioned the TLS escalation on `NEED_SECURED` while
asserting the pinning unconditionally, and it passes the guard.
Verified by restoring the pre-sweep text and running the guard
over it.

Making the test per-sentence was tried and is worse. It flags
the correct negative claim at `openstack.md:45`, a
cross-reference at `:310`, and continuation sentences whose
condition sits in the sentence before them -- four hits on a
tree with nothing wrong in it. Block granularity is the right
trade; the limit is inherent, not a tuning failure.

The guard is therefore worth having and worth not trusting.
Its header comment and the `docs/testing.md` passage both name
the `shakenfist.md` miss specifically rather than carrying a
general caveat, so the next person reads the limit rather than
a reassurance.

### shaken-fist.md became shakenfist.md

Not a phase 4 finding. Asked for directly while the branch was
still open, and taken here rather than on a branch of its own
because most of the files that reference the page are files
this phase already touches. The new name matches
`kerbside/sources/shakenfist.py` and the GitHub organisation;
the prose name "Shaken Fist" is unchanged.

Every reference a reader would follow to find the page now
points at the new name: `README.md`, `docs/index.md`,
`docs/console-sources.md`, `docs/testing.md`, the See also
section of all five sibling pages, the guard script's header
comment, and the master plan's survey citation. This plan is
rewritten with them, because it ships in the same pull request.

The phase 1 to 3 plan files are not, and neither is
`PLAN-use-case-docs-phase-01-shaken-fist.md`'s own name. Those
are merged history describing a file that was called
`shaken-fist.md` at the time they described it, and rewriting
them to match a later rename would make the record less true
rather than more. The `console-sources.md#shaken-fist` anchor
is untouched -- it is generated from a heading that still reads
"Shaken Fist", and no file was renamed under it.

### What review found, and what it cost the guard

One round, nine items, all taken: three `fix`, two `document`
and four `consider`. Taking every `consider` is unusual and was
not a failure to triage -- each was a defect in something this
phase added, which is the test for taking an optional item,
rather than an observation about code that was already there.

Two of the three were about the guard, and both are worse than
the limitation this plan had already recorded:

- **It never ran on the pull requests it was for.** The step
  sat in `sanity_checks`, which is gated on
  `check_paths.code_changed`, and that filter excludes
  `docs/**`. A documentation-only pull request skipped the job
  entirely. This one ran it only incidentally, because it also
  touches `tools/` and `.github/`. It is now its own ungated
  `docs_checks` job, for the same class of reason
  `credential_scan` is ungated.
- **The conditional vocabulary was hollow.** `configured` named
  no condition but exempted every block containing it, on pages
  that say "configured cloud" and "configured source"
  constantly. Review demonstrated it with a bald claim that the
  guard passed. `should` and `none` went with it.

The deeper problem was that neither could have been caught,
because roughly 110 lines of Python lived in a shell heredoc:
flake8 never saw it and nothing could import it, so the proof
was a one-off manual mutation that did not survive into CI. It
is now `tools/check-backend-tls-claims.py` with
`kerbside/tests/unit/test_check_backend_tls_claims.py` beside
it, following `tools/check-pypi-storage.py` and its test.

Writing that test found four more things the review had not,
which is the argument for it: the guard still double-reported
one bullet as two findings, an inherited comment claimed an
exclusion that never worked, stripping underscore emphasis
turned `host_subject` into `hostsubject` in both the matching
and the output, and nothing at all proved `should`, `none` or
`docs/index.md`'s inclusion mattered. The mutation tool exists
so the next regex edit cannot quietly undo any of it.

The glob was **not** widened to `docs/**/*.md`, which review
offered as an alternative. Measured: it produces six hits on a
clean tree of which one is a real claim, because
`verify-terminate-live.sh` matches the crypto vocabulary, a
task "pinned forever" matches the pinning vocabulary, and a
list of `Target` fields matches both. Silencing five false
positives by loosening regexes is exactly how the guard came to
miss things in the first place. `docs/index.md` was added on
its own instead, because its Use Cases table describes these
pages and had drifted the same way.

**Filed as #472, not deferred to a plan section.**
`docs/proxy-architecture.md:62` says the Shaken Fist
`host_subject` "is pinned at scrape time from the hosting
node's published SPICE server certificate subject" -- the sixth
instance of the same overstatement, in a reference page this
phase's sweep did not scope. It is the one real hit the
widening experiment found. Round two of review pointed out
that recording it in a plan file, with nothing in the tree
tripping on it, is the exact situation that produced the five
previous instances, which is right.

### Round two, and a sentence this branch got wrong

One `fix`, one `document`, four `consider`, four `none` -- down
from three `fix`. All six actionable items taken.

The `fix` is the one worth recording, because round one caused
it. Narrowing the conditional vocabulary correctly tripped on a
run-on in `shakenfist.md` that mixed hypervisor reachability
with the API certificate, and the sentence written to split it
was wrong in both halves. It said the API certificate "is
checked against the CA named in the source configuration",
where `_build_client` (`kerbside/sources/shakenfist.py:19-23`)
hands the client no CA at all. And it said that CA "has nothing
to do with the backend leg above", where it is precisely the
backend leg's CA: `kerbside/rpc/servicer.py:136` carries it
into `Target.ca_cert` and `rust/kerbside-proxy/src/backend.rs:
198-202` uses it there. The configured `ca_cert` is instead
compared for equality against the CA the API publishes
(`shakenfist.py:70-78`).

That is the error class this phase exists to eliminate,
introduced by the commit that eliminates it, in prose reworded
to satisfy the guard rather than to state what the code does.
The guard did not catch it, because `checked` is outside the
crypto vocabulary. The lesson is the one the phase kept
learning: read the code for the sentence you are writing, not
only for the sentence you are fixing.

The rest were defects in what round one added. The guard
skipped headings entirely, so a claim written as one was never
examined -- a hole outside the limits the docstring states,
which is worse than a stated one. `default_paths()` globbed
bare relative patterns while `main()` did the `chdir`, so it
returned `[]` from any other working directory and its test
passed only because stestr runs from the root. The mutation
tool raised `FileNotFoundError` when `.tox/py3` was not built,
and `docs/testing.md` named the test without naming the tool
that proves it. And `multi-cloud.md`'s failure-mode row cited
only the SSL path, where every exception but `NotFoundException`
ends the request -- the page that owns the mechanism stating it
more narrowly than the page linking to it.

### Round three, and stopping

Zero `fix`, one `document`, five `consider`, four `none`. Six
actionable items, all taken, none of them about the prose: the
mutation tool reported eleven false "caught" lines when the tox
environment was not built and then blamed the backup, an
explicit path argument that did not exist traced back rather
than naming itself, the `docs_checks` rationale comment read as
a fragment, `openstack.md` kept "Order therefore matters" after
the antecedent moved to `multi-cloud.md`, and this plan said
the mutation tool breaks the guard "nine ways" when it is
eleven. That count is now gone rather than corrected, so it
cannot drift again.

The one judgement call is item 4, which offered a choice: run
the mutation tool in CI, or say in `docs/testing.md` that it is
manual. Taken as the documentation half. `docs/testing.md` now
says plainly that no lane runs it, because claiming an
enforcement that does not exist is exactly the round-one
mistake, and adding a CI lane in the third review round of a
documentation phase is how a review loop stops converging.

Stopping here. The exit rule is no `fix` items rather than the
reviewer running out of things to say, and this round met it
while the remaining items are all `consider`. Trajectory across
the three rounds: three `fix`, then one, then none; the middle
round's `fix` was caused by the first round's change, which is
the signal that mattered.

## Registration

The planning commit corrects the three stale claims at source,
so a later step does not redo it:

- The master plan's `Multi-cloud aggregation` proposed-page row
  is corrected per survey finding 1: the scenario is now stated
  on three pages, not one, and `openstack.md` carries the
  substantive version.
- The master plan's `Placement topologies` row is corrected per
  survey finding 3: the backend leg is TLS'd when the
  hypervisor demands it and subject-pinned when the source
  supplies a subject, which OpenStack never does.
- The `docs/plans/index.md` description for this plan is
  updated to match, and its phase 3 fragment moves to complete
  with its merge commit.

## Back brief

Before executing any step, back brief the operator on the
understanding of this plan.

**Gate on the back brief before step 4a.** Two pages are cheap
to propose and expensive to redo, and decision 3 deletes
material from a document phase 2 shipped a week ago. The back
brief should state, in its own words: what goes on the
multi-cloud page and what stays on `openstack.md`; what the
placement page is allowed to claim about the backend leg and
under what conditions; and why the sweep in 4d is in this phase
rather than phase 5. If any of those three comes back
differently from the plan, resolve it before any file is
written.
