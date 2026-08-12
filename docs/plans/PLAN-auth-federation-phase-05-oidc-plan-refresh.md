# Phase 5 — OIDC plan refresh

Planning effort: **medium**, reviewed at **high**. The master plan
sets medium because this is documentation only. The review is high
because the deliverable's whole value is that a reader can trust it
about the codebase, and a plausible-sounding falsehood in a planning
document is invisible until someone builds on it.

## Scope

Rewrite `docs/plans/PLAN-oidc-authentication.md` — the human-login
sibling plan, currently 429 lines of stub — so that it plans forward
from what phases 1 to 4 actually built rather than from the
pre-federation codebase it was written against.

In scope: every section of that one file, plus its rows in
`docs/plans/index.md` and the phase 5 rows in the master plan.

Out of scope, explicitly:

* **Any code change.** Not one line. If the rewrite turns up a bug,
  it is filed as an issue and noted, per Decision 6.
* **Deciding the direct-bearer versus exchange question.** The master
  plan reserves that for the OIDC plan's own phase 0. Phase 5's job
  is to pose it well enough that phase 0 can settle it. See
  Decision 4, which is the one most likely to be argued with.
* **Cutting any OIDC phase plan.** This phase produces a master plan
  that is ready to have phases cut from it, and stops there.
* **Touching `docs/plans/order.yml`.** Both master plans are already
  registered; phase files never are.

## What the survey found

The master plan's phase 5 section makes four factual claims about what
phases 1 to 4 shipped. Three hold. One is an overstatement, and it is
the load-bearing one, so it is corrected here rather than discovered
by the implementing agent. The survey also turned up a security
interaction that nothing in either plan currently records, and which
changes the shape of the new open question the master plan asks for.

### The stub's phase 2 is only half superseded — there is no discovery

The master plan says the stub's "tentative phases 1–2 (JWT validation
refactor; OIDC validator with discovery/JWKS) are marked superseded by
this plan's phase 3". The JWKS half is superseded. **The discovery
half was never built.**

`TrustedIssuer` carries exactly three attributes — `issuer_url`,
`jwks_uri` and `audience` (`shakenfist/schema/trusted_issuer_attributes.py:19-53`)
— and `jwks_uri` is a required argument to
`TrustedIssuer.new()` (`shakenfist/trusted_issuer.py:131`), validated
as a mandatory `https://` string at
`shakenfist/external_api/auth.py:742-758`. Nothing in the tree fetches
`.well-known/openid-configuration`; the only `well-known` strings are
literal GitHub JWKS URLs in a swagger example
(`shakenfist/external_api/auth.py:726`) and in tests. That the JWKS
location is never taken from the token is a deliberate security
property, documented at
`shakenfist/schema/trusted_issuer_attributes.py:31-33`, and it is not
in question here — discovery would populate the field at configuration
time, not at validation time.

Why this matters enough to correct rather than gloss: discovery is not
a convenience for the human-login plan, it is a prerequisite. The
device-code flow the stub's open question 9 leans on needs the
issuer's `device_authorization_endpoint` and `token_endpoint`, and the
discovery document is where a relying party is supposed to learn them.
GitHub Actions never needed this because the workflow already holds a
minted token and Shaken Fist only ever verifies it. A human client has
to *start* a flow, which means it needs endpoints nothing currently
stores. So the rewritten plan must keep a discovery phase rather than
striking it, and should say plainly that phase 3 built the verification
half of an OIDC relying party and none of the client half.

### A token with no `scopes` claim is treated as fully privileged

`api_scopes.satisfies()` returns `True` unconditionally when the held
scope list is `None` (`shakenfist/external_api/scopes.py:138-145`),
and `caller_is_admin` tests the same predicate
(`shakenfist/external_api/base.py:140-146`). The reasoning is sound
and is written down at `shakenfist/util/access_tokens.py:30-41`: a
token minted before the claim existed carries no `scopes`, and
refusing those would have invalidated every token in flight across an
upgrade.

The interaction nobody has had to think about yet is that **an
IdP-issued token also carries no `scopes` claim.** Under the stub's
central design — humans present the IdP's JWT directly as a bearer
token — such a token would reach `_enforce_scope`
(`shakenfist/external_api/base.py:1234`) holding `None` and satisfy
every scope, including `cluster-admin`. The backward-compatibility
argument that justifies the default applies only to tokens Shaken Fist
itself minted, which have a history; an externally-issued token has
none.

This is not an argument that direct-bearer is wrong. It is a concrete,
already-existing constraint that the direct-bearer option has to
answer — the missing-claim default would have to become issuer-
dependent rather than global — and it belongs in the new open question
as evidence rather than being left for whoever writes phase 0 to
rediscover. It is recorded here as a design constraint, not filed as a
bug: no IdP token can reach that path today, because the only way in
is `/auth`, which mints its own token.

### Namespace keys: the stub's Situation is wrong, and the trap is subtle

The stub's Situation item 1 says keys live in the
`namespace_attributes.keys` JSON column. Phase 2 moved them to the
`namespace_keys` and `namespace_key_attributes` tables
(`shakenfist/namespace_key.py:62`), and the JSON column is described
in its own docstring as "neither read nor written any more"
(`shakenfist/namespace.py:187-190`).

The trap: `Namespace.keys` still *synthesises* the legacy
`{'nonced_keys': {...}}` dict from key objects
(`shakenfist/namespace.py:197-205`), and six live call sites still
read that synthetic shape, including `/auth` itself
(`shakenfist/external_api/auth.py:184-185`). An agent that greps
`nonced_keys` to check the stub's claim will find plenty of hits and
may conclude the claim still stands. It does not: those hits are a
compatibility view over the tables, not the column.

### One claim in the stub is right and must not be "corrected"

The stub says the JWT identity is `<namespace>:<keyname>`. The code
reads `f'{ns.uuid}:{keyname}'`
(`shakenfist/util/access_tokens.py:48-51`), which looks like a
contradiction and is not: `Namespace` overrides `uuid` to return the
name, because namespaces are keyed by name rather than by UUID
(`shakenfist/namespace.py:61-68`). The stub is correct as written. An
agent tidying it into "namespace uuid" would introduce an error into a
document whose purpose is to be trustworthy about the codebase. The
master plan's own Situation section says "`<namespace uuid>:<keyname>`",
which is true of the source line and misleading about the value; leave
the master plan alone, since it is describing the code and is not
wrong, but do not propagate the phrasing.

### What else phases 1 to 4 changed under the stub's feet

Recorded here so the briefs can reference it rather than re-deriving
it. Each is a statement in the stub that is now false, incomplete, or
answered:

| Stub text | As built |
|---|---|
| Situation 1: keys are a JSON attribute | `namespace_keys` + `namespace_key_attributes` tables, with expiry, scopes and provenance (`shakenfist/namespace_key.py:62`, `shakenfist/schema/namespace_key_attributes.py:24-70`) |
| Situation 5: inter-node uses `_service_key*` | Still true, and now on the new tables with `sfk_`-format secrets and a 5 minute token (`shakenfist/namespace.py:386-410`). The nonce-check carve-out is only for the exact name `_service_key` (`shakenfist/external_api/base.py:589-597`) |
| "Outsourcing to a real IdP is currently impossible" | False since phase 3: `POST /auth/federated` (`shakenfist/external_api/app.py:383`) |
| OQ 1, issuer trust model: "possible resolution: support a list" | Resolved, and as objects rather than config: `TrustedIssuer`, admin-managed under `/auth/issuers` (`shakenfist/trusted_issuer.py:41`) |
| OQ 3, token shape: "tokens carry `sub` and a `nonce`" | Also `iss` and `scopes` (`shakenfist/util/access_tokens.py:42-47`). The `verify_token` refactor it asks for partly happened: authentication is now universal via `Resource.method_decorators` (`shakenfist/external_api/base.py:1288-1296`) with `@api_base.public` the only way out. `request_namespace()` is still a string split (`shakenfist/util/access_tokens.py:76`) and still used in roughly forty places, so the "per-request authorisation decision" half is untouched |
| OQ 4, audience | Per-issuer `audience` field, required, verified with required claims `['exp','iss','aud']` and zero leeway (`shakenfist/federation.py:321-340`) |
| OQ 5, admin as a claim | Partly resolved by phase 3: `caller_is_admin` now requires the `system` namespace **and** the `cluster-admin` scope (`shakenfist/external_api/base.py:128-148`). What remains is whether the namespace half can be dropped in favour of the claim alone |
| OQ 7, revocation | The nonce is unchanged for SF tokens. For an externally-issued token there is still no equivalent — but phase 3 established a third option the stub does not consider: exchange the external token for a key, and inherit nonce revocation for free |
| OQ 11, service-account rename | Phase 2 made keys first-class objects but did **not** rename them. The question is now purely a UX one, since the schema migration it worried about has already happened for other reasons |
| Tentative phase 1, JWT validation refactor | Superseded: `shakenfist/federation.py` validates external tokens, with a pinned RS/ES/PS algorithm allowlist and HS deliberately absent (`shakenfist/federation.py:39-51`) |
| Tentative phase 2, OIDC validator | JWKS fetch, caching and rotation superseded (`shakenfist/federation.py:120-210`). **Discovery not built** — see above |
| Tentative phase 5, service-account rename | Still open, now cosmetic |
| Tentative phase 6, CLI flows and token cache | Entirely out of this repository — see below |

### Several remaining phases are not changes to this repository

`sf-client` lives in the separate `client-python` repository; this
repo ships only `sf-ctl` and `sf-backup`
(`pyproject.toml:157-158`). A grep of `client-python` for OIDC,
login or device-code support finds nothing, and its authentication is
still `_authenticate()` posting a namespace and key to `/auth`
(`shakenfist_client/apiclient.py:333-335`).

So the stub's tentative phase 6 is wholly a client-python change, and
phase 3's three route families are not wrapped by the client either —
which the master plan already records as future work. The rewritten
Execution table must say, per row, which repository the work lands in.
A plan that silently mixes them produces a phase nobody can execute in
the checkout they are standing in.

### Nothing else was found to be wrong

The master plan's other three claims about phase 5 hold: the stub is
still a stub, its open question 1 is genuinely resolved by the
trusted-issuer objects, and its open question 11 does need re-answering
in terms of key objects. The stale claims listed above are corrected
at their source as part of this planning commit — the master plan's
phase 5 section now says the discovery half of the stub's phase 2
survives, and the `index.md` phase 5 row says the same. Step 5d
verifies that rather than redoing it.

## Decisions

**Decision 1 — the stub is rewritten in place, keeping its path.**
`PLAN-oidc-authentication.md` is registered in `docs/plans/order.yml`,
has a row in `docs/plans/index.md`, and is linked from the master plan
in two places and from the glossary's surrounding prose. A new file
would strand all of that for no gain. The rewrite is wholesale — this
is not an edit pass — but the filename, and therefore every inbound
link, is untouched.

**Decision 2 — the plan stops being a stub, and says so structurally.**
Today the document announces itself as a placeholder in three separate
places (its open questions preamble, its Execution note, its Agent
guidance section). After phases 1 to 4 there is enough known to write a
real plan: the infrastructure exists, the vocabulary is pinned, and the
remaining work is genuinely enumerable. So the Agent guidance section
is filled in properly rather than left as a promise, and the
`index.md` status moves from `Stub` to `Not started`, which is the
status the table already uses for real-but-unstarted plans (67 rows
use it).

**Decision 3 — superseded phases are struck through with their
successor named, not deleted.** A reader arriving at this plan in six
months needs to know that JWT validation was built, and where. Deleting
the rows makes the plan read as though validation were still to do the
moment anyone forgets. Each superseded row keeps its text, gains a
`Superseded` status, and names what replaced it — for the two
tentative phases affected, `shakenfist/federation.py` and phase 3 of
the auth federation plan. The half of tentative phase 2 that was *not*
built keeps a live row of its own.

**Decision 4 — the direct-bearer versus exchange question is posed,
not answered, but it is posed with evidence.** This is the decision
most likely to be argued with, because the skill this phase was
planned under says a plan that defers every choice is not a plan, and
here the central architectural question is deliberately deferred.

The distinction being drawn: phase 5's own decisions are about the
*document*, and they are made here. The direct-bearer question is
*content* of the document, and the master plan explicitly reserves it
for the OIDC plan's phase 0 decisions pass. Pre-empting it in a
documentation phase would settle a security-architecture question
without the codebase reading, IdP research and functional-test thinking
that a real decisions pass would bring, and would settle it in a
document nobody would then re-examine.

What phase 5 does owe is a question worth answering. A two-column
"on the one hand" comparison is not that. So the new open question must
carry, at minimum: the missing-`scopes` interaction above, stated as a
constraint direct-bearer must answer; the observation that exchange
inherits nonce revocation and direct-bearer inherits only the IdP's
`exp`, which is the stub's own open question 7 reappearing as a
consequence of the choice; the multi-namespace problem, which cuts the
other way, since a human is typically in several namespaces and every
credential Shaken Fist mints today names exactly one
(`shakenfist/util/access_tokens.py:50`, `shakenfist/schema/namespace_key_data.py:60-64`);
and a statement of what evidence would settle it.

**Decision 5 — multi-namespace access is raised to a first-class open
question.** The stub treats "we hand the full list in the token" as
settled design, in one sentence, in its Situation section. The survey
shows it is the single largest piece of unbuilt work in the plan:
`parse_jwt_identity()` requires exactly two colon-separated components,
`request_namespace()` returns one string, and around forty call sites
across the API compare its result against an object's namespace. That
is not a detail of the claim-mapping phase, it is a change to the
shape of every authorisation check in the codebase, and it interacts
with trust (`namespace_is_trusted()`) in ways nobody has thought
through. It gets its own open question and its own phase row.

**Decision 6 — findings are recorded, not fixed, and not inflated into
issues.** The missing-`scopes` interaction is unreachable today, so it
is a design constraint on future work rather than a defect: it goes in
the plan, not in the issue tracker. Filing it would advertise a
security-shaped concern about code that does not have the problem, and
would age badly the moment phase 0 chooses exchange. If the rewrite
turns up something genuinely reachable, that is an issue and phase 5
stops to raise it.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | high | opus | none | Rewrite the **Situation** and **Mission and problem statement** sections of `docs/plans/PLAN-oidc-authentication.md`, and its Prompt section's file references. Read the whole file first, then read this phase plan's *What the survey found* table, which front-loads the research — do not re-derive it, but do spot-check any line you intend to write a file reference for. The Situation must describe the world as it is: namespace keys are first-class objects with expiry, scopes and provenance; trusted issuers and mapping rules are objects; `POST /auth/federated` exists and exchanges an external identity token for a scoped key; authentication is universal via `Resource.method_decorators` with `@api_base.public` the only exemption; and `caller_is_admin` already requires a `cluster-admin` scope as well as the `system` namespace. Frame all of it as *existing infrastructure this plan builds on*, with pointers to `docs/glossary.md` anchors for `identity token`, `trusted issuer`, `mapping rule`, `namespace key`, `access token`, `scope`, `nonce` and `trust` — the anchors exist in the form `#trusted-issuer`, check them rather than guessing. Keep the stub's "What this model gets right / does not give us" framing, but re-cut both lists: "outsourcing to a real IdP is currently impossible" is now false, while "no human SSO story" and "no central account lifecycle" are still exactly true. **Do not** change `<namespace>:<keyname>` to say uuid — read this plan's note on why it is already correct. Do not touch the open questions or Execution table; 5b and 5c own those. Commit subject: "docs: refresh the OIDC plan's situation." |
| 5b | high | opus | none | The open questions pass over the same file, and the heart of this phase. Every one of the stub's twelve open questions must be dealt with explicitly and none may vanish: mark it **Resolved** with the mechanism and file reference that resolved it, **Partly resolved** with what precisely remains, or leave it open with its text tightened now that the surrounding infrastructure is known. Use this phase plan's table as your starting map — questions 1 and 4 are resolved, 3, 5, 7 and 11 are partly resolved or re-framed, and 2, 6, 8, 9, 10 and 12 remain genuinely open. Follow the master plan's own house style for this, which is to append a bold **Resolved by ... (date)** paragraph under the original text rather than rewriting the question away; read `docs/plans/PLAN-auth-federation.md` open questions 1 to 11 for the pattern, including that it records consequences that were *not* anticipated. Then add two new open questions per Decisions 4 and 5: the direct-bearer versus exchange-based-session question, carrying all four pieces of evidence Decision 4 lists, and the multi-namespace question. Both must end with a sentence naming what evidence would settle them. Do not answer either. Commit subject: "docs: re-answer the OIDC plan's open questions." |
| 5c | medium | opus | none | Rewrite the **Execution** table, fill in **Agent guidance**, and refresh **Success criteria** and **Future work** in the same file. The Execution table gains a `Repo` column: rows landing in `client-python` must say so, and per this phase plan's survey that is at minimum the CLI flow and token cache work, which cannot be done in this checkout at all. Apply Decision 3 to the two superseded rows — keep the text, set the status to `Superseded`, name `shakenfist/federation.py` and auth federation phase 3 as what replaced them. Add a live row for OIDC discovery, which was *not* built and which the device-code flow depends on; add a row for the multi-namespace authorisation change per Decision 5. Fill in Agent guidance properly rather than leaving the current "to be filled in" placeholder: mirror the structure of `docs/plans/PLAN-auth-federation.md`'s Agent guidance, with an execution model, per-phase planning effort, and a review checklist whose entries are specific to this plan — at minimum that no phase weakens the `satisfies()` missing-claim default without saying so, and that inter-node authentication is never put behind an external IdP. Re-read Success criteria against the new table and delete any criterion phases 1 to 4 already satisfy. Commit subject: "docs: replan the OIDC execution phases." |
| 5d | medium | opus | none | Closeout, and it is mostly a reading and checking step. First, read `PLAN-oidc-authentication.md` end to end as one document and confirm 5a to 5c compose — three agents writing three sections produces seams, and the specific failure to look for is the Situation describing infrastructure as existing while a later section still plans to build it. Second, verify the two plans nowhere disagree about the codebase: read the master plan's Situation, its phase 3 and phase 5 sections, and its open questions 1 to 11 against the rewritten stub, and fix the stub where they differ (the master plan is the more recently reviewed document and wins on any conflict, except where this phase plan records it as loosely worded). Third, run these checks and report their output rather than summarising it: `grep -n "namespace_attributes.keys\|currently impossible\|placeholder\|stub status" docs/plans/PLAN-oidc-authentication.md` must return nothing; `grep -c "^[0-9]\+\." docs/plans/PLAN-oidc-authentication.md` in the open questions section must show fourteen questions, being the original twelve plus two. Glossary anchors need no manual check: `tools/check-doc-anchors.py` is already a pre-commit hook over `^docs/.*\.md$`, which includes this file, so a cited anchor that does not exist fails the commit. Fourth, set the `index.md` row for the OIDC plan from `Stub` to `Not started` per Decision 2, mark phase 5 Complete in the master plan's Execution table and in `docs/plans/index.md`, and verify — do not redo — that the master plan's phase 5 section already records that the discovery half of the stub's phase 2 survives. Commit subject: "docs: OIDC plan refresh closeout." |

After each step the management session reads the diff against the
brief and confirms no unrelated edits, and `pre-commit run
--all-files` must pass. There are no Python changes in this phase, so
a diff touching a `.py` file is by itself a failed step.

## Risks and mitigations

- **Risk:** the rewrite reads as confident and is wrong somewhere,
  which is worse than the stub being obviously stale. A planning
  document nobody distrusts is exactly where an error survives.
  **Mitigation:** every factual claim about the codebase carries a
  file reference, so review is a spot-check rather than a memory
  test; the management session picks three at random per step and
  opens them. The survey table in this plan is the source for the
  claims, and it was built by reading the code rather than the
  earlier plans.

- **Risk:** an agent "corrects" the `<namespace>:<keyname>` identity
  into `<namespace uuid>:<keyname>`, introducing an error while
  appearing to fix one. This is a genuine trap: the source line does
  say `ns.uuid`.
  **Mitigation:** called out in *What the survey found*, in 5a's
  brief, and here. The management session greps the diff for `uuid`
  in 5a.

- **Risk:** 5b quietly drops an open question. Twelve is enough that a
  missing one is not noticed by reading.
  **Mitigation:** the count is a done-criterion and 5d checks it
  mechanically. Fourteen out, twelve of them traceable to a specific
  original.

- **Risk:** the direct-bearer question is posed so even-handedly that
  it carries no information, which is the standard failure mode of
  "lay out the trade-offs fairly".
  **Mitigation:** Decision 4 enumerates the four things it must
  contain, and requires a closing sentence naming what evidence would
  settle it. The management session reviews that question specifically
  rather than as part of the whole file, and the test applied is
  whether a phase 0 reader could act on it.

- **Risk:** three agents write three sections and the document has
  seams — most likely the Situation and the Execution table
  disagreeing about what exists.
  **Mitigation:** 5d is a whole-document read with that exact failure
  named, and the back brief gate below agrees the skeleton before any
  writing starts.

- **Risk:** phase 5 widens into fixing the things it finds. The
  missing-`scopes` interaction is the obvious candidate, since it
  looks like a security bug and the fix looks small.
  **Mitigation:** Decision 6, and the fact that it is unreachable
  today. If the management session disagrees, it becomes its own
  phase with its own review — not a step here.

## Definition of done

- [ ] `docs/plans/PLAN-oidc-authentication.md` contains no statement
      contradicted by the codebase, spot-checked against the survey
      table in this plan, with a file reference on every claim about
      what exists.
- [ ] All twelve of the stub's original open questions are accounted
      for — each marked resolved with its resolving mechanism named,
      marked partly resolved with the remainder stated, or left open
      with tightened text. None deleted.
- [ ] Two new open questions exist: direct-bearer versus
      exchange-based sessions, carrying the four pieces of evidence
      Decision 4 lists, and multi-namespace authorisation. Neither is
      answered. Both end by naming what evidence would settle them.
- [ ] The Execution table names, per row, which repository the work
      lands in, and the CLI flow and token cache rows say
      `client-python`.
- [ ] The two tentative phases superseded by auth federation phase 3
      are marked `Superseded` and name what replaced them; a live row
      exists for OIDC discovery, which was not built.
- [ ] The plan nowhere says namespace keys live in
      `namespace_attributes.keys`, nowhere says outsourcing to an IdP
      is impossible, and nowhere describes itself as a stub or
      placeholder — `grep -n "namespace_attributes.keys\|currently
      impossible\|placeholder\|stub status"` returns nothing.
- [ ] The plan still says the JWT identity is
      `<namespace>:<keyname>`, because that is correct.
- [ ] Agent guidance is filled in, not promised, and its review
      checklist names the `satisfies()` missing-claim default and
      inter-node independence from the IdP.
- [ ] Every `docs/glossary.md` anchor cited by the rewrite resolves,
      which `tools/check-doc-anchors.py` enforces as a pre-commit
      hook rather than by inspection.
- [ ] The two plans nowhere disagree about the codebase, confirmed by
      reading the master plan's Situation, phase 3, phase 5 and open
      questions against the rewrite.
- [ ] `docs/plans/index.md` shows the OIDC plan as `Not started`
      rather than `Stub`, and phase 5 Complete in both the master
      plan and `index.md`.
- [ ] `docs/plans/order.yml` is unchanged.
- [ ] `pre-commit run --all-files` passes, and the diff contains no
      `.py` files.

## Back brief

Before executing any step, the implementing sub-agent must back brief
the management session on its understanding of the brief and the
surrounding context.

For 5a specifically the back brief must include the proposed section
skeleton for the whole rewritten document — section order, and a
one-line disposition for each of the twelve existing open questions
(resolved / partly resolved / open). Agreeing that before any prose is
written is cheap; discovering in 5c that the Situation and the
Execution table were built on different assumptions is not. This
mirrors the gate phase 4 put in front of its own restructure step.
