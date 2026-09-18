# Phase 1: `docs/use-cases/shaken-fist.md`

Master plan: [PLAN-use-case-docs.md](/components/kerbside/plans/PLAN-use-case-docs/)

Planned at **high effort**. The page is mostly writing, but the
judgement it turns on is not: `docs/console-sources.md` already
documents the Shaken Fist flow in eighty lines of accurate detail,
and the whole risk of this phase is producing a second copy of it
that drifts. Deciding what this page says instead is the work.

Review effort: **medium**. The master plan sets no review effort
for any page; oVirt's equivalent (two-tier CI phase 4) was
reviewed at medium and that is the precedent.

## Situation

The master plan proposed seven use-case pages. One exists —
`docs/use-cases/ovirt.md`, landed 2026-08-10 as
`PLAN-two-tier-ci-phase-04-docs.md`'s deliverable — and it settles
the format, the directory, and the index placement. This phase
writes the second, for Shaken Fist.

Shaken Fist is the strongest candidate to go next. Its underlying
work is finished and proven: `PLAN-kerbside-vdi-tokens.md` is
`Complete`, the `/sf-console.vv` offline exchange is implemented
and documented, and `sf-e2e-functional.yml` exercises the whole
path on every pull request and nightly. Nothing about the page
depends on work that has not landed.

## Mission

A reader who runs Shaken Fist can decide whether Kerbside is worth
deploying, understand the broker/token/connection flow well enough
to reason about it, and set it up — without this page restating
what `console-sources.md` and `configuration.md` already own.

## Scope

In:

- `docs/use-cases/shaken-fist.md`, following the oVirt page's
  section order and its "Status and limitations" table form.
- The inbound links that make it reachable: the `Shaken Fist` row
  of `docs/index.md`'s Use Cases table, the curated list in
  `README.md`, and the Related Documentation list in
  `docs/console-sources.md`.
- Correcting the four master-plan claims the survey found stale,
  at their source (see below) — done in the planning commit, not
  left for an implementation step.

Out:

- The other five pages. OpenStack, standalone/static, multi-cloud
  aggregation and placement topologies are phases 2 to 4; Proxmox
  stays deferred and is not a phase.
- Slimming `docs/index.md`'s introduction. The master plan wants
  the OpenStack and Bumblebee material moved out of the intro,
  but the OpenStack page is its destination, so it belongs to
  that phase and then the closeout. Moving it now would leave it
  homeless.
- Any change to `kerbside/sources/shakenfist.py` or to the
  reference pages' substance. If the page cannot be written
  truthfully without a code or reference-doc change, that is a
  finding to record, not to fix here.
- New CI coverage. The page describes the lane that exists.

## What the survey found

The master plan's page list was written on 2026-08-02, before the
oVirt page, before the VDI tokens plan completed, and before the
install rewrite. Six things moved. The structural decisions — the
`docs/use-cases/` directory, the four-section format, the "Status
and limitations" table — all survived unchanged, and the Shaken
Fist row's own description ("Broker embedded in SF; Ed25519 VDI
console tokens; the sf-e2e lane is the worked example") is
accurate in every particular.

1. **This is a standalone plan with no phases, and the skill
   assumes a master plan.** `PLAN-use-case-docs.md` has no
   Execution table, and `docs/plans/index.md` carries it in the
   *Standalone plans* table, not the master plan one. Promoting
   it is decision 1; the consequences, including the push-audit
   phase it acquires, are set out there.

2. **`docs/plans/order.yml` does not exist in this repository.**
   The skill names it as the master-plan registry and says not to
   touch it for a phase file. There is nothing to touch. The
   registries here are the master plan's own Execution table and
   `docs/plans/index.md`.

3. **The index scaffolding is already built, which the master
   plan does not know.** `docs/index.md:148-173` already carries a
   `### Use Cases` heading with a seven-row table — every proposed
   page including Proxmox — each with a description and a "Tested
   in Kerbside CI" column, plus the line "Scenarios without a link
   are planned rather than written". So this phase does not add a
   row; it links an existing one. The Shaken Fist row already
   reads "Broker embedded in Shaken Fist itself; Ed25519 VDI
   console tokens exchanged offline at `/sf-console.vv`" and names
   `sf-e2e`, smoke tier and nightly. The page must agree with that
   row or change it, and changing it is in scope.

4. **"The remaining six pages are unblocked" is wrong by the
   plan's own table.** The Proxmox row says "Deferred until the
   source exists", and it still does not: `kerbside/sources/`
   holds `base.py`, `ovirt.py`, `shakenfist.py` and `static.py`,
   and nothing else. Five pages are unblocked, not six. Corrected
   in the master plan.

5. **The static-source row's precondition has been met.** It
   defers the demo mechanics to `docs/installation.md` "per
   PLAN-demo-install.md decision 2", which was still a promise
   when written. It landed 2026-08-22: `docs/installation.md:160`
   is `## Try it: the demo stack`, and `docs/index.md`'s
   standalone row already links `installation.md#try-it-the-demo-stack`.
   Phase 3 inherits a settled boundary rather than an open one.
   Corrected in the master plan to past tense.

6. **The multi-cloud row's "stated nowhere" is now very nearly
   false.** `grep -rn -i 'multi-cloud\|several sources\|aggregat'`
   across `docs/*.md`, `docs/use-cases/`, `README.md` and
   `ARCHITECTURE.md` returns exactly one substantive hit: the
   index table row added since. The oVirt page also states it in
   one bullet ("One entry point across clouds",
   `docs/use-cases/ovirt.md:41-45`). The gap the master plan
   describes is real but is now one sentence and one bullet wide,
   not absent. Corrected in the master plan.

The load-bearing finding is not in that list, because it is not a
staleness — it is a hazard the master plan never anticipated:

**`docs/console-sources.md` already documents the Shaken Fist flow
in full, and better than a use-case page should.** Lines 67-146
are eighty lines covering the cluster-wide `system` scrape, the
offline Ed25519 exchange with `aud`/`exp`/`jti` single-use, the
key-rotation refetch, exactly which rejections are audited and why
the pre-verification ones are not, `host_subject` pinning from
`spice_server_cert_subject`, the `synthesize_host_subject` knob
and its PKI caveat, and the signing-key fetch failure mode
including `sf-ctl ensure-kerbside-signing-key` and the 404 from
`/admin/vditokenpubkey`. That text is `PLAN-kerbside-vdi-tokens.md`
phase 8 step B1's deliverable and was written deliberately. A
"How it works" section that re-narrates it creates two copies of
a security-relevant description that will disagree within a
release. Decision 3 is the answer.

Everything else the plan assumes checks out: `PLAN-kerbside-vdi-tokens.md`
is `Complete`, `sf-e2e-functional.yml` runs on `pull_request`,
`merge_group` and nightly as a smoke-tier gate
(`docs/testing.md:76`, `:405-421`), `tools/sf-e2e/` holds the
driver scripts with their own README, and `Can enqueue: sf-e2e` is
one of the five required checks (`docs/testing.md:155`).

## Decisions

1. **Promote `PLAN-use-case-docs.md` from a standalone plan to a
   master plan with an Execution table, in this phase's planning
   commit.** Six pages cannot be tracked by a single `In progress`
   cell, and the repository has done exactly this before —
   `PLAN-consistency-audit.md` is annotated "Promoted from a
   standalone plan". Two consequences, both accepted rather than
   discovered later. First, `docs/plans/index.md` moves the row
   from the Standalone table to the Master plans table, which
   means it gains a Phases column. Second, and more significant:
   `PLAN-TEMPLATE.md`'s `plan-push-audit-phase v3` shared block
   binds every master plan to a final push-audit phase over the
   accumulated diff. That is not optional and it is not free, so
   it is phase 6 in the table from the start, and each phase
   records its merge commit as it lands, because the block is
   explicit that the range is not reliably reconstructable
   afterwards. A documentation-only plan still carries it; the
   block's whole point is that silently omitting it is what let
   the audit go untriggered before.

2. **Shaken Fist is phase 1, ahead of OpenStack.** This is the
   decision most open to argument, so the reasoning is worth
   stating. The case for OpenStack first is good: the master plan
   wants `docs/index.md`'s introduction slimmed, and the intro's
   two movable sections (`### Implementation in OpenStack` and
   `### What About Bumblebee?`, lines 106-145) are both OpenStack
   material, so doing it first starts paying down the index
   immediately. I am choosing against it. The oVirt page is a
   single data point for a format that six more pages must fit,
   and the second instance should be the one with the least
   unsettled ground under it, so that a format problem surfaces
   as a format problem. Shaken Fist qualifies and OpenStack does
   not: the OpenStack page has to describe a deployment story
   that is still moving upstream — `docs/index.md:127-133` says
   Kolla has merged the OCI build but not the Kolla-Ansible
   deployment code, which is still on Gerrit — so its "how to set
   it up" section will need judgement about what to promise. That
   is a worse second page. It is a fine third.

3. **The page links the flow; it does not re-narrate it.** "How
   it works" gets the diagram, the actors, and the sequence at the
   altitude the oVirt page uses — enough that a reader can reason
   about where the trust sits and what crosses which network —
   and then sends them to `docs/console-sources.md#shaken-fist`
   for the per-option and per-failure-mode detail. Concretely: the
   page may state that verification is offline, single-use and
   Ed25519-signed, because that is the value proposition. It must
   not restate the `jti` replay table, the pre- versus
   post-verification audit split, the key-rotation refetch, the
   `synthesize_host_subject` PKI caveat, or the
   `sf-ctl ensure-kerbside-signing-key` failure mode. Those have
   an owner. The test in the definition of done is mechanical: no
   sentence of `console-sources.md` appears in substance on both
   pages.

4. **The page is written against the deployed reality, and the
   sf-e2e lane is the worked example.** The oVirt page's "A
   worked example" section points at the CI ansible. Shaken Fist's
   equivalent is `tools/sf-e2e/`, which stands up a single-node
   cluster and a Kerbside against it on every pull request. That
   is a stronger worked example than oVirt's, because it is a
   smoke-tier gate rather than a merge-tier one, and the page
   should say so.

5. **The "Status and limitations" table says what is not proven,
   one row per claim, per the master plan's own instruction.** At
   least these are known: multi-node Shaken Fist is not covered by
   any lane (`PLAN-two-tier-ci.md:365-367` lists a multinode SF
   lane as future work), and the `synthesize_host_subject` path
   exists precisely because some clusters publish no
   `spice_server_cert_subject`. The implementer adds what else
   the code and the lane will not support, and each row names why.

6. **No new CI coverage, and no change to the index row's CI
   column.** `sf-e2e` already covers this scenario and the row
   already says so. If writing the page shows the row's claim is
   wrong, that is a finding for the back brief, not a silent edit.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high | opus | none | Write `docs/use-cases/shaken-fist.md`. **Read first, in this order:** `docs/use-cases/ovirt.md` in full (293 lines — it is the format authority: sections are Value proposition, How it works, How to set it up with Engine side / Network / Kerbside side / A worked example subsections, User interaction model, Status and limitations, See also; note that "How it works" opens with a mermaid `flowchart TD` and that "Status and limitations" is a table of what is *not* proven, one row each, naming why); `docs/console-sources.md:67-146` (the Shaken Fist section — this is what you must **not** duplicate, see decision 3); `kerbside/sources/shakenfist.py`; the `/sf-console.vv` handler in `kerbside/api.py`; `tools/sf-e2e/README.md` and the scripts beside it; `docs/testing.md:405-421`. **The page must cover:** the value proposition against Shaken Fist's native console story (Kerbside is the broker embedded in SF's own flow, not a bolt-on); how the cluster-wide `system`-credential scrape populates the inventory; the `/sf-console.vv` exchange at the altitude decision 3 permits — offline Ed25519 verification, single use, no callback to the cloud on the hot path, and *why that matters* (the cloud can be down and consoles still open, and a leaked token is worthless twice) — then link to `console-sources.md#shaken-fist` rather than detailing it; `host_subject` pinning on the backend leg; setup split into Shaken Fist side, network, and Kerbside side; `tools/sf-e2e/` as the worked example, noting it is a smoke-tier PR gate; and a Status and limitations table per decision 5. **Constraints:** relative links from `docs/use-cases/` need `../` (see how ovirt.md links `../proxy-architecture.md`); mermaid diagrams are linted in CI by `tools/mermaid-lint.sh`, so run it; wrap prose at the width ovirt.md uses; do not touch any other file in this step. |
| 1b | medium | sonnet | none | Wire up the inbound links, four files, no prose invention — each is an edit to an existing line. (i) `docs/index.md:162` — the `| Shaken Fist | ... |` row of the Use Cases table: turn the bare scenario cell into a markdown link whose text stays `Shaken Fist` and whose target is the page path relative to `docs/`, exactly mirroring how the oVirt row at `:159` links its own page. (This plan states it that way rather than showing the literal link because a link written here would be resolved relative to `docs/plans/` — see the note under this table.) Leave the description and CI columns alone unless step 1a's page contradicts them, in which case stop and report rather than editing. (ii) `README.md:43` — the curated list currently has one use-case entry, `Kerbside for oVirt`, described as "The first of the per-deployment guides". Add a Shaken Fist entry beside it and reword oVirt's "first of" phrasing, which stops being true. Use the absolute `https://github.com/shakenfist/kerbside/blob/develop/docs/use-cases/shaken-fist.md` form every link in that list uses. (iii) `docs/console-sources.md` — add the new page to the `## Related Documentation` list at line 333, so the reference page points back at the use-case page. (iv) `docs/use-cases/ovirt.md` — its `## See also` section at line 284 should name the sibling page. **Do not** add a row to any table or touch `docs/index.md`'s introduction. |
| 1c | low | sonnet | none | Verification pass, no edits except to fix what it finds. Run, from the repository root: `tools/mermaid-lint.sh` (the new page's diagram must render); `grep -rn 'use-cases/shaken-fist' docs/ README.md` (expect hits in `docs/index.md`, `docs/console-sources.md`, `docs/use-cases/ovirt.md` and `README.md`); and for every relative link in the new page, resolve it from `docs/use-cases/` and confirm the target file and anchor exist — an anchor is a heading in the target, lowercased with spaces as hyphens and punctuation dropped. Then `pre-commit run --all-files`. Report anything that fails; do not paper over a broken anchor by deleting the link. |

Steps run in order. 1a is the whole phase; 1b and 1c are
mechanical and exist so that 1a's brief can say "do not touch any
other file", which is the cheapest way to stop a writing step from
wandering into the index.

One gotcha, found by running the checks against this plan and
recorded because it will recur in phases 2 to 4:
`kerbside/tests/unit/test_docs_links.py` resolves every relative
`.md` link in every tracked markdown file, plan files included,
against that file's own directory. A plan cannot therefore contain
a literal markdown link to the page it is planning to create — it
resolves against `docs/plans/`, does not exist yet, and fails
`tox -e py3`. Describe such a link instead of writing it, as step
1b now does.

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| The page duplicates `console-sources.md` and the two drift apart, leaving two descriptions of a security control. | The primary risk of the phase, and decision 3 is the control. Checked at review, not by the implementer: read the new "How it works" against `console-sources.md:67-146` and confirm no fact about the exchange is *stated* on both pages rather than stated once and linked. The definition of done names the six specifics that may not cross over. |
| The page overstates what is proven, because the sf-e2e lane is green and it is tempting to read that as coverage of everything. | The lane is single-node. Decision 5 makes the "Status and limitations" table mandatory and the definition of done requires the multi-node gap to appear in it. Reviewer checks that row exists against `PLAN-two-tier-ci.md:365-367`. |
| The format does not in fact generalise, and forcing the Shaken Fist material into the oVirt section order produces a worse page than a different order would. | This is the reason Shaken Fist is second rather than sixth — see decision 2. If it happens, the back brief is the place to say so, and the outcome is a corrected format recorded in the master plan for phases 2-4, not a one-off deviation in this page. |
| Promoting the plan to a master plan acquires a push-audit phase that nobody costed. | Stated in decision 1 rather than discovered at phase 5, and carried in the Execution table from the start. Each phase records its merge commit as it lands, because the shared block is explicit that the range cannot be reconstructed reliably afterwards. |
| `README.md` grows a bullet per use-case page, against the readme-discipline policy that new features are documented in `docs/`, not added as README bullets. | The policy permits touching README when the curated doc links change, which is exactly this. But six more bullets is the wrong end state. Step 1b adds the second entry and rewords oVirt's "first of the per-deployment guides"; the closeout phase should collapse them to a single link to the Use Cases index section. Recorded here so phase 5 inherits it. |

## Definition of done

Each item is checkable by someone who did not write the page.

- [ ] `docs/use-cases/shaken-fist.md` exists and carries, in
      order, Value proposition, How it works, How to set it up,
      and Status and limitations. Extra sections are fine; a
      missing one is not.
- [ ] None of these six appear on both `docs/use-cases/shaken-fist.md`
      and `docs/console-sources.md`: the `jti` replay table, the
      pre- versus post-verification audit split, the signing-key
      rotation refetch, the `synthesize_host_subject` PKI caveat,
      the `sf-ctl ensure-kerbside-signing-key` 404, and the
      per-option table. The use-case page links to
      `console-sources.md#shaken-fist` for all six.
- [ ] Omitting those six has not become *asserting their
      negation*. Round 1 of review caught exactly this: the page
      said "the verification path does not call Shaken Fist",
      which the rotation refetch at
      `kerbside/sf_token.py:185-193` makes false, and the false
      claim was load-bearing for both the availability argument
      and the "offline" security property. The rule forbids
      describing a mechanism; it does not licence denying that
      the mechanism exists. Where an exception is material, name
      that it exists and link. Phases 2 to 4 inherit this.
- [ ] "Status and limitations" is a table, one row per unproven
      claim, each naming why — and one of the rows is the
      single-node limit of the sf-e2e lane.
- [ ] `tools/mermaid-lint.sh` exits zero, and the page has at
      least one diagram.
- [ ] `grep -rln 'shaken-fist\.md' docs/ README.md` returns all
      four of `docs/index.md`, `docs/console-sources.md`,
      `docs/use-cases/ovirt.md` and `README.md`. Match on the
      bare filename, not on `use-cases/shaken-fist`: a sibling
      link from inside `docs/use-cases/` is correctly written
      with no directory prefix, so the longer pattern can never
      hit `ovirt.md` and reports a false failure. Phases 2 to 4
      copy this check, so they should copy this form of it.
- [ ] Every relative link in the new page resolves to an existing
      file, and every anchor to an existing heading in that file.
- [ ] `README.md` no longer calls the oVirt page "the first of
      the per-deployment guides".
- [ ] `docs/index.md`'s introduction is unchanged by this phase.
- [ ] `pre-commit run --all-files` passes.
- [ ] The master plan's Execution table and the plan's row in
      `docs/plans/index.md` both say `Complete` for phase 1, and
      the Execution row records the merge commit.

## Registration

Done in the planning commit, not deferred:

- `PLAN-use-case-docs.md` gains an Execution table with six
  phases, per decision 1, and its four stale claims are corrected
  in place (survey findings 4, 5 and 6, plus the note that the
  index scaffolding already exists).
- `docs/plans/index.md` moves the plan's row from Standalone
  plans to Master plans, links this phase file, and describes
  what the survey found.
- There is no `docs/plans/order.yml` in this repository to
  update. See survey finding 2.

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.

Two gates, both cheap to raise now and expensive to undo later:

- **Before step 1a writes anything**, state in one paragraph what
  the page will say about the token exchange and what it will
  leave to `console-sources.md`. Decision 3 draws that line and
  the whole phase turns on it; agreeing the split before the
  prose exists is much cheaper than unpicking a duplicated
  security description afterwards.
- **If step 1a concludes the oVirt section order does not fit
  Shaken Fist**, stop and say so rather than deviating quietly.
  The format is meant to bind five more pages, so a change to it
  is a master-plan decision, not a page-level one.
