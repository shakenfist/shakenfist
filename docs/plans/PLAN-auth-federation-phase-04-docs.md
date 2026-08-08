# Phase 4 — authentication documentation

Planning effort: **medium**, reviewed at **high**. The master plan
says why: the "don't reveal the conductor" constraint is a judgement
call on every page, and judgement calls are what review is for.

## Scope

Bring `docs/{developer,operator,user}_guide/authentication.md` and
`docs/glossary.md` into line with what phases 1 to 3 actually built,
and read each guide end to end rather than trusting that three phases
of incremental additions composed into a coherent page.

Documentation only. No behaviour changes, no new endpoints, no client
changes. Where the survey found a gap that documentation cannot
honestly paper over, this phase records it and raises an issue rather
than quietly widening into an implementation phase — see Decision 2.

## What the survey found

The master plan's phase 4 section was written before phase 3 executed
and two of its premises are now wrong. Both are load-bearing, so they
are corrected here rather than discovered by the implementing agent.

### The user guide page exists

The master plan says `docs/user_guide/authentication.md` "does not
exist at all" and "needs writing from scratch". It exists, at 34
lines, and `docs/plans/index.md` repeats the same claim in its phase 4
row. Both need correcting as part of this phase.

What is true is that the page predates every one of phases 1 to 3. It
describes a world with no expiry, no scopes, no federation and no
`sfk_` format: a namespace has keys, a key is a string, put it in
`~/.shakenfist`. Nothing in it is false, which is the problem — it
reads as current and is silently three phases stale. It also carries
four typos in 34 lines ("home direct", "not real rules imposed", "key
not reuse an existing one", "the the Shaken Fist command line").

So this is a rewrite of a live page, not a green field. That is
harder, not easier: the existing content is the only authentication
documentation a user is pointed at, and the rewrite has to keep
working for the reader who just wants to know where the config file
goes.

### Expiry and scopes surface nowhere a user can see

The master plan asks the user guide to explain "how expiry and scopes
surface in `sf-client`". They do not surface at all, in the client or
in the REST API.

`NamespaceKey.external_view()` (`shakenfist/namespace_key.py:321`)
returns `namespace`, `name`, `expiry`, `scopes` and `provenance`, and
is documented as "the operator visible view of a key". No endpoint
ever calls it. Grepping `external_view` across
`shakenfist/external_api/auth.py` finds namespaces, trusted issuers
and mapping rules served, and no keys.

What is served instead is `AuthNamespaceKeysEndpoint.get` at
`shakenfist/external_api/auth.py:471`, which returns a bare list of
key *names*, and reads them from the legacy
`namespace_from_db.keys['nonced_keys']` dict rather than from the key
objects at all:

```python
out = []
for keyname in namespace_from_db.keys.get('nonced_keys', {}):
    out.append(keyname)
return out
```

On the client side, `sf-client namespace add-key` takes `NAMESPACE
KEY_NAME KEY` as three mandatory positional arguments, with no
`--expiry` and no `--scopes`. `apiclient.get_namespace_keynames()`
returns names. A grep for `scope`, `expiry`, `federat` and `issuer`
across `shakenfist_client/` finds one unrelated match in a docstring.

Two consequences. A namespace owner cannot ask which of their keys
expires when, or what a federated key is allowed to do, by any means
short of reading the database. And the operator guide's advice to
"create the key without supplying a secret" to get a generated `sfk_`
secret is REST-only — the CLI's `KEY` argument is not optional, so
following that paragraph with `sf-client` is impossible.

The master plan already records `sf-client federation ...` and
`sf-client namespace add-key --expiry` as client-python follow-ups.
The missing *read* path is new, is server-side, and is the more
serious of the three, because it is the one that makes the other two
hard to verify.

### The worked example is in the wrong guide

The master plan places the worked GitHub Actions example in the
operator guide. Phase 3 shipped it in the developer guide, at
`docs/developer_guide/authentication.md:492`, and the operator guide's
federation section describes issuer and rule fields in prose and
tables without a single executable example.

That is backwards for the reader. Configuring an issuer is an
administrative act the operator guide already claims as its own
territory ("what an operator has to decide and configure"), and the
person who needs the `curl` is the one being told they need to decide.

### The developer guide's spine is pre-federation

The page opens "Shaken Fist uses JWT tokens for authentication and
access control" and proceeds through namespaces, `sfrc`, key
management, `/auth`, token contents and inter-node authentication
before reaching Scopes at line 312 and Federated identity at line 404.
The new material is correct and well written; it is also bolted to the
end of a document whose opening paragraph, section order and worked
examples all describe the system as it was before any of it existed.

A reader who stops half way — which is what a reader does — comes away
believing a key is a string with no expiry, no scopes and no
provenance. This is exactly what the master plan meant by re-reading
the guides end to end rather than assuming incremental additions
compose.

Two smaller instances of the same drift: the `Key management` section
still gives `sf-client namespace add-key namespace-name keyname key`
as the only way to make a key, with no mention of cluster generation
or expiry; and `Key Storage` describes the two tables without
mentioning that scopes and provenance now live in them.

### The conductor constraint is nearly satisfied already

The master plan requires that nothing in `docs/` describe or depend on
the private CI conductor. Grepping `docs/` outside `docs/plans/` and
`docs/components/` finds exactly one mention, at
`docs/operator_guide/authentication.md:279`:

> when our CI conductor creates a new CI runner and associated
> namespace, it creates a trust between that ephemeral namespace and
> the `ci-images` namespace

That is a narrative about how the Shaken Fist project uses trusts. It
names the conductor but reveals nothing about how it works, and the
paragraph it sits in is a genuinely useful worked motivation for
trusts. See Decision 3.

### The glossary is in good shape

Phase 1's glossary was updated during phase 3 and its entries for
identity token, mapping rule, namespace key, scope and trusted issuer
describe behaviour in the present tense. The header note at
`docs/glossary.md:10` already records that the *(planned)* markers
were removed as of phase 3. This phase needs to cross-link it, not
rewrite it.

## Decisions

**Decision 1 — the user guide page is rewritten in place, and keeps
its current job.** The rewrite leads with what the existing page
leads with (you have a namespace, you have a key, here is where the
file goes) because that is what most readers arrive for, and adds
expiry, scopes and federated keys after it. It does not become a
second developer guide. The test for including something is whether a
user who does not administer the cluster can act on it.

**Decision 2 — the visibility gap is documented and filed, not
fixed here.** Making `GET /auth/namespaces/{namespace}/keys` return
key objects rather than a list of strings is a breaking change to a
published response shape; `sf-client` and the Ansible modules both
consume it. That needs a compatibility story — a second endpoint, a
query parameter, or a major version — and it is an implementation
question, not a documentation one. Phase 4 raises an issue, adds a
Future work entry to the master plan, and writes the guides to say
plainly what can and cannot be inspected today. Documentation that
describes a facility the reader cannot reach is worse than
documentation that admits the gap.

**Decision 3 — the conductor sentence stays, genericised.** Deleting
the `ci-images` narrative would cost a good explanation of why trusts
exist to satisfy a constraint aimed at something else. The constraint
protects the conductor's *internals*; a sentence saying the project's
CI creates a namespace per job and trusts a shared image namespace
describes a pattern any reader can copy. Reword to "our CI system"
rather than "our CI conductor" so no reader goes looking for a
component they cannot see, and leave the rest.

**Decision 4 — the worked example moves to the operator guide, and
the developer guide keeps the protocol.** The operator guide gets the
issuer `curl`, the rule `curl` and the workflow YAML. The developer
guide keeps the eight-step ordering, the claim matching rules, the
single-use semantics and the reasoning about why the order is a
security property, and links across. Neither page should hold both;
the current duplication risk is that they drift.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | opus | none | Rewrite `docs/user_guide/authentication.md`. It exists at 34 lines and predates phases 1 to 3; read it first, and keep its opening job — a user arrives wanting to know what a namespace and a key are and where `~/.shakenfist` goes. Fix the four typos ("home direct", "not real rules imposed", "key not reuse", "the the"). Then add, in user terms: that a key may expire and what that looks like when it happens (a 401 on a credential that worked yesterday, and that tokens already minted last out their nominal fifteen minutes); that a key may carry scopes and that a 403 on an endpoint you can otherwise reach means the key is scoped rather than that you lack the namespace; that a federated key is an ordinary key which arrived by a different route and behaves identically once you hold it; and that secrets the cluster generates start with `sfk_`. **Do not** claim any of this is visible from `sf-client` — it is not, per Decision 2. Say explicitly that a key's expiry and scopes cannot currently be listed and that the namespace owner or cluster administrator is the source of truth, and link the operator guide. Keep the existing links to the developer guide and glossary. Prose style matches the other user guide pages: second person, short paragraphs, no tables of settings. Commit subject: "docs: bring the user authentication guide up to date." |
| 4b | high | opus | none | Re-spine `docs/developer_guide/authentication.md`, all 588 lines, as one document rather than an original plus three phases of appendices. Read it end to end first. The opening paragraph frames the system as JWT tokens and must instead frame it as: keys are objects, tokens derive from keys, both carry scopes, and keys may be minted by exchange as well as by an administrator. Section order should follow that shape, which means Scopes and Federated identity stop being sections 12 and 13. Fold the fixes into the re-read: `Key management` must mention cluster generation and expiry alongside `add-key`, and `Key Storage` must mention that scopes and provenance live in `namespace_key_attributes`. Move the worked GitHub Actions example out to the operator guide per Decision 4 and 4c, replacing it with a link — keep the eight-step exchange ordering, the claim matching discussion and the single-use semantics here, since those are protocol, not configuration. Preserve the reasoning in the existing prose; it was written carefully and the security arguments in particular (why steps 1 to 3 precede 4, why the meter is step 2, why `HS256` is refused) must survive verbatim in substance. This is a restructure, not a rewrite: if you find yourself improving sentences that were already correct, stop. Commit subject: "docs: restructure the developer authentication guide." |
| 4c | medium | opus | none | Read `docs/operator_guide/authentication.md` end to end and close its gaps. Receive the worked GitHub Actions example from 4b — the issuer `curl`, the rule `curl`, and the workflow YAML with its `id-token: write` permission and `::add-mask::` line — and site it in the Federated identity section, after "Delegating to namespace owners" and before "Abuse resistance", so the reader meets the fields as prose and then sees them used. Keep the note that `sf-client` does not wrap these routes. Apply Decision 3: reword `docs/operator_guide/authentication.md:279` from "our CI conductor" to "our CI system" and leave the surrounding `ci-images` narrative intact. Add a short subsection under "Cluster generated key secrets" noting that generation is REST-only today because `sf-client namespace add-key` requires a secret argument, so an operator following that advice needs `curl`. Add a note to the key expiry section that expiry cannot presently be listed back — `GET /auth/namespaces/{namespace}/keys` returns names only — with a link to the issue raised in 4d. Do not restructure the page; it is coherent, and its Trusts section in particular is recent and correct. Commit subject: "docs: complete the operator authentication guide." |
| 4d | low | sonnet | none | Bookkeeping, no prose writing. (1) Raise a GitHub issue titled "Namespace key expiry, scopes and provenance are not readable through any API", describing what `NamespaceKey.external_view()` returns, that no endpoint serves it, that `AuthNamespaceKeysEndpoint.get` returns names from the legacy `keys['nonced_keys']` dict, and that changing that response shape breaks `sf-client` and the Ansible modules so it needs a compatibility story. (2) Add a Future work entry to `docs/plans/PLAN-auth-federation.md` for that read path, referencing the new issue, next to the existing `sf-client federation ...` and `add-key --expiry` entries. (3) Cross-link the glossary from any of the three guides that has gained a section without one — the header link exists on all three today, so check rather than assume. The false premises in the master plan's phase 4 section and the `index.md` phase 4 row were corrected when this plan was written; verify they still read correctly rather than re-editing them. Commit subject: "docs: file the key visibility gap." |
| 4e | medium | opus | none | Closeout, and it is a reading step rather than a writing one. Read all three guides in the order a person would meet them — user, then operator, then developer — and check three properties. First, no fact is stated differently in two places: expiry semantics, the fifteen minute token life, `NAMESPACE_KEY_REAP_GRACE`, and the `sfk_` format all appear on more than one page and must agree. Second, every forward reference resolves: the guides link to each other by anchor and 4b renumbers the developer guide's sections, so every `#anchor` needs checking against the file it points at. Third, nothing in `docs/` outside `docs/plans/` and `docs/components/` names the CI conductor — re-run the grep, since 4c edits that line. Then mark phase 4 Complete in the master plan's Execution table and in `docs/plans/index.md`. Commit subject: "docs: authentication guide closeout." |

After each step the management session reads the diff against the
brief and confirms no unrelated edits. `pre-commit run --all-files`
must pass after every step; it is fast here because no Python changes,
but the markdown and workflow linters still run. After 4b
additionally: a manual read confirming no security argument was lost
in the restructure, since that is the failure mode a diff makes hard
to see.

## Risks and mitigations

- **Risk:** 4b's restructure silently drops one of the security
  arguments phase 3 wrote down, because a large reordering diff is
  unreadable as a diff.
  **Mitigation:** the brief names the four arguments that must
  survive, and the management session re-reads rather than diffs.
  If it helps, extract the section headings before and after and
  compare those first.

- **Risk:** the user guide becomes a second developer guide. It is the
  easiest failure here, because the interesting material is all
  developer material.
  **Mitigation:** Decision 1's test — can a reader who does not
  administer the cluster act on it — plus a hard look at the line
  count. The current page is 34 lines. If the rewrite exceeds about
  120, something has been included that belongs elsewhere.

- **Risk:** documenting the visibility gap reads as an apology and
  ages badly once the gap is closed.
  **Mitigation:** state it as current behaviour with an issue link,
  in one sentence, in the two places a reader would look. Do not
  editorialise about it.

- **Risk:** moving the worked example breaks inbound links. The
  developer guide anchor `#a-worked-github-actions-example` may be
  linked from elsewhere in the repository or from the release notes.
  **Mitigation:** 4c greps for the anchor before moving it, and 4e
  re-checks every cross-guide anchor after the renumbering.

- **Risk:** phase 4 quietly becomes phase 4-plus-an-API-change,
  because the visibility gap is annoying and small to fix.
  **Mitigation:** Decision 2 is explicit, and the breaking response
  shape is the reason. If the operator disagrees and wants it fixed
  now, it should be a separate phase with its own compatibility
  design, not an extra step here.

## Definition of done

- [ ] `docs/user_guide/authentication.md` describes expiry, scopes,
      federated keys and the `sfk_` format in user terms, is free of
      the four existing typos, and claims nothing about `sf-client`
      that `sf-client` cannot do.
- [ ] `docs/developer_guide/authentication.md` reads as one document:
      its opening frames keys as objects with scopes and provenance,
      and a reader who stops half way is not left with the
      pre-federation model.
- [ ] Every security argument phase 3 recorded in the developer guide
      is still present after the restructure — specifically the
      exchange ordering, the placement of the rate limit meter, the
      refusal of `HS256`, and why claim matching is exact.
- [ ] `docs/operator_guide/authentication.md` carries the worked
      GitHub Actions example, and it is the only copy.
- [ ] The example stands alone for a reader running their own
      runners, and nothing in `docs/` outside `docs/plans/` and
      `docs/components/` names the CI conductor.
- [ ] The key visibility gap is filed as an issue, noted in the
      master plan's Future work, and stated in the two guides where a
      reader would look for it.
- [ ] The master plan's phase 4 section and the `docs/plans/index.md`
      phase 4 row no longer claim the user guide page does not exist
      (corrected when this plan was written; verify, do not redo).
- [ ] Every cross-guide anchor resolves after the developer guide's
      sections are renumbered.
- [ ] No fact about expiry, token lifetime, reaping or the `sfk_`
      format is stated differently on two pages.
- [ ] `pre-commit run --all-files` clean. No Python, proto or
      behaviour changes in the diff.
- [ ] Phase 4 marked Complete in the master plan's Execution table
      and in `docs/plans/index.md`.

## Back brief

Before executing any step of this phase, the implementing sub-agent
must back brief the management session on its understanding of the
brief and the surrounding context.

For 4b specifically the back brief must include the proposed section
order for the developer guide, agreed before any editing starts.
Reordering a 588 line document is cheap to propose and expensive to
redo.
