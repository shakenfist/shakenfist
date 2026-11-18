# Phase 6: serving namespace key metadata

Planning effort: **medium**, as
[the master plan](PLAN-oidc-authentication.md) specifies. No review
effort is specified for this phase, so the default applies.

## Context

A namespace key has carried an `expiry` since the auth federation
plan's phase 2, and `scopes` and `provenance` since its phase 3.
`NamespaceKey.external_view()`
(`shakenfist/namespace_key.py:404-419`) renders all five fields and
calls itself "the operator visible view of a key". No endpoint calls
it. What is served instead is a bare list of key names.

So a namespace owner cannot answer "which of my keys expires when", or
"what is this federated key actually allowed to do", without reading
the database. That is
[issue #3672](https://github.com/shakenfist/shakenfist/issues/3672),
and it is what blocks operating a key as a service-account credential:
a credential whose expiry and scopes cannot be read back cannot be
audited or rotated on purpose.

Phase 4 made this sharper rather than easier. It mints a credential set
at human login, and each minted key's `provenance` now records the
person's subject identifier, the token's `jti` and the claims they
satisfied. The audit question #3672 names is therefore now also a
question about a person, which is why this phase's central decision is
not "serve the view" but "serve which fields, to whom".

## Scope

In scope, in this repository:

* Two new reads: a `GET` on the existing single-key route, and an
  opt-in detailed mode on the existing collection route.
* A caller-sensitive projection of a key, and the privilege rule that
  decides which fields a caller sees.
* One `API_CAPABILITIES` token advertising the pair.
* Moving the two existing bare-name listings off the accessor
  documented as authentication-only.
* Correcting `external_view()`'s docstring, which phase 4 made false.
* Documentation: the new reads, and removal of the three interim notes
  that say this cannot be done.

In scope, in `client-python`:

* `apiclient` support for both reads, and `sf-client namespace show`
  rendering the detail when the cluster advertises it.

Explicitly out of scope:

* **Changing the existing bare-name response shape.** Both the
  collection endpoint and `Namespace.external_view()`'s `keys` field
  keep answering with a list of names. See decision 1.
* **A rename of keys.** Settled as "no rename" by open question 11
  before this phase was cut.
* **Any storage change**: no new column, no new table, no migration.
* **A secret-free database accessor.** See decision 6.
* **`sf-client namespace add-key --expiry`**, which #3672 mentions as a
  neighbouring client gap. It is a write, this phase is about reads,
  and it needs no server change. Recorded as future work.

## What the survey found

The master plan's phase 6 section was written at commit `6b0ab81b2a`,
before phase 4 executed. Six of its claims needed checking and three
were wrong or incomplete. Corrections were made at source; see
*Corrections made at source* below.

### 1. `external_view()` is exactly as described, and its docstring is now false

The method is at `shakenfist/namespace_key.py:404` and renders
`namespace`, `name`, `expiry`, `scopes` and `provenance` over the base
view's `uuid`, `state`, `version` and `metadata`. That much the master
plan has right.

Its docstring (`shakenfist/namespace_key.py:407-408`) says the view is
"safe to hand to anyone who can see the namespace". That was true when
it was written. Phase 4 then put a person's `sub`, their token's `jti`
and the claims they satisfied into `provenance`
(`shakenfist/external_api/auth.py:3257-3270`), and the same phase's own
code comment four lines earlier states the opposite rule:

> Who logged in is recorded in provenance, which an administrator
> reads, rather than in the name, which everyone who can list the
> namespace's keys can see.
> — `shakenfist/external_api/auth.py:3244-3247`

Those two comments cannot both be obeyed. A phase that serves
`external_view()` on the docstring's authority publishes who logged in
to everyone who can list the namespace. Decision 2 resolves it and step
6a corrects the docstring, because leaving it would invite the next
reader to make the same mistake with the same justification.

### 2. The line reference drifted, and the "legacy view" framing is wrong

The master plan cites `AuthNamespaceKeysEndpoint.get` at
`shakenfist/external_api/auth.py:591-595`. It is now at
`auth.py:637-653`, with the handler body at 649-653. Phase 4 inserted
code above it.

More substantially, both the master plan and #3672 describe the handler
as reading "the legacy `nonced_keys` compatibility view" or "the legacy
`keys['nonced_keys']` dict rather than from the key objects at all".
That is no longer true, and the difference matters. `Namespace.keys`
(`shakenfist/namespace.py:175-214`) *synthesises* the `nonced_keys`
dict on every call from the real key objects, via
`keys_with_attributes()`. Its own docstring says so: "The legacy
`namespace_attributes.keys` JSON column is neither read nor written any
more; it is vestigial until a later schema version drops it."

So `nonced_keys` is a legacy *wire shape*, not a legacy *store*. There
is no stale data to migrate off and no reconciliation to worry about —
the objects behind the names are already the authoritative ones. This
makes the phase smaller than its description implies, and it removes
the only reason the work might have needed a storage step.

### 3. There are two bare-name listings, and the user-facing one is not the endpoint the plan names

The master plan says the obstacle is the keys endpoint, and names
`sf-client namespace show` as what renders the new fields. Those are
not connected. `namespace show`
(`client-python`, `shakenfist_client/commandline/namespace.py:66-126`)
never calls the keys endpoint. It calls `get_namespace()` and reads
`ns['keys']`, which is served by `Namespace.external_view()`
(`shakenfist/namespace.py:469-486`) — a second bare-name listing out of
the same synthesised dict.

The keys endpoint's only in-tree consumer is
`apiclient.get_namespace_keynames()`
(`client-python`, `shakenfist_client/apiclient.py:1616-1618`), and
**no CLI command calls that at all**; its only caller is one unit test
(`shakenfist_client/tests/test_client_apiclient.py:262-265`).

A phase that changed only the keys endpoint would therefore leave every
user-facing surface exactly as it is. #3672's body does identify both
consumers, so the issue is more complete than the master plan's row —
the row is what needed correcting.

### 4. The disclosure audience is wider than "a namespace owner"

`requires_namespace_ownership` (`shakenfist/external_api/auth.py:119`)
admits a caller when `namespace_is_trusted(ns, request_namespace())`
holds. That function (`shakenfist/namespace.py:677-688`) returns true
when the caller's namespace *is* the target, or when it appears in the
target's `trust` list. Every namespace is created with
`trust = ['system']` (`shakenfist/namespace.py:170`) and the system
trust cannot be removed (`shakenfist/namespace.py:332`).

So the audience for a key listing is: the namespace itself, `system`,
and every namespace the target has granted full trust to. That is the
set decision 2 has to size the disclosure against, and it is not the
same thing as "an administrator".

### 5. Phase 4 did not ship any of this phase's scope, but it did set a field vocabulary

`POST /auth/login` returns one entry per minted key, built explicitly
rather than from `external_view()`
(`shakenfist/external_api/auth.py:3306-3312`):

```python
out.append({
    'namespace': rule.namespace,
    'key_name': key_name,
    'key': secret,
    'expiry': expiry,
    'scopes': list(policy.scopes)
})
```

That serves metadata only for keys the response is simultaneously
handing out the secrets for, which is the one moment a secret may be
disclosed and is not a readable-back view of anything. So the phase's
premise holds: nothing serves a key's metadata.

It does mean a key appears in two shapes in one API. The login entry
spells the name `key_name`, matching the key create response
(`auth.py:656-660`'s swagger) and the route parameter; `external_view()`
spells it `name`. Decision 3 picks one.

### 6. A single-key route already exists with no `GET`

`AuthNamespaceKeyEndpoint` (`shakenfist/external_api/auth.py:690`) is
routed at `/auth/namespaces/<namespace>/keys/<key_name>`
(`shakenfist/external_api/app.py:719-720`) and implements `put` and
`delete` only. A `GET` there is a new method on an existing route,
which is additive by construction — no existing client can be reading a
response that does not exist. This is the cheapest half of decision 1
and the master plan does not mention the route is already there.

### 7. The accessor to use, and the one not to

`keys_with_attributes()` (`shakenfist/namespace_key.py:425-454`) reads
one namespace's keys as static rows joined to their attributes rows in
a single query, with the expiry filter pushed into SQL.

`NamespaceKeys` (`shakenfist/namespace_key.py:457`) is the `dbo_iter`,
and its `_find` applies any state filter with "a point read per key"
(`shakenfist/namespace_key.py:467-472`, the comment is explicit). A
listing endpoint built on it with a state prefilter is an N+1 on a path
a namespace owner can call freely. Decision 6 forbids it.

The two accessors also disagree on expired keys by default:
`keys_with_attributes` takes `include_expired=False`, `NamespaceKeys`
takes `include_expired=True`. Decision 4 settles what this phase
serves.

### 8. Three interim notes say this is impossible, and all three must go

#3672's body says phase 4 of the auth federation plan "states the
limitation in the three authentication guides" and that "those notes
should be removed when this is closed". They are:

| File | Lines | What it says |
|---|---|---|
| `docs/operator_guide/authentication.md` | 40-51 | The keys endpoint answers with names only; the forensic window matters more because of it; tracked as #3672 |
| `docs/developer_guide/authentication.md` | 157-161 | "a key's expiry, scopes and provenance are recorded on the object but are not readable through any API" |
| `docs/developer_guide/api_reference/authentication.md` | 214, 235, 280 | Three worked examples calling `get_namespace_keynames` |

`docs/developer_guide/ci_api_coverage.md:199` also carries a
`get_namespace_keynames` section. That page is **manually** maintained --
its own header says so -- so a new client method with no entry makes the
page understate the API surface rather than report it untested. The two
new methods get `*Not tested.*` entries, which the page treats as a
legitimate answer.

### `file:line` drift in the master plan

Beyond item 2, the master plan's phase 6 paragraph cites
`shakenfist/namespace_key.py:404-418`; the method now ends at 419.
Nothing else in that paragraph carries a line reference.

### Corrections made at source

Made as part of this phase's planning commit, so a later step does not
redo them:

* The master plan's phase 6 paragraph: the `auth.py:591-595` reference
  corrected to `auth.py:637-653`, the `namespace_key.py` range to
  `404-419`, the "legacy `nonced_keys`" framing replaced with the
  synthesised-shape fact from survey item 2, and the second consumer
  from survey item 3 added, since the paragraph as written points the
  phase at the one endpoint nothing user-facing reads.
* `docs/plans/index.md`'s row for the plan: the description now says
  what phase 6 found rather than restating phase 4.

The master plan's `Repo` column, planning-effort entry and review
checklist for this phase were all correct and are untouched.

## Decisions

**D1. Two new additive reads, not a changed response shape.** #3672
names three possible compatibility stories — a second endpoint, an
opt-in query parameter, or a major version bump. This phase takes the
first two together and rejects the third:

* `GET /auth/namespaces/<namespace>/keys/<key_name>` — a new method on
  an already-routed resource (survey item 6). Additive by construction.
* `GET /auth/namespaces/<namespace>/keys?detail=true` — the existing
  collection, unchanged without the parameter.

The collection needs the parameter because "which of my keys expires
when" is a listing question, and answering it with one request per key
is the N+1 this repository keeps paying down. The single-key read is
worth having anyway: it is the natural shape, it costs nothing to add,
and it is what a client checking one key after a write should call.

A version bump is refused because this repository's mechanism for "can
I rely on this" is `API_CAPABILITIES`, not a version number, and
because it would break every existing client to serve a field three of
them do not read.

**D2. `provenance` is served to `system` only. `expiry` and `scopes`
are served to any caller who can read the namespace.** This is the
decision this phase turns on, and it is a deliberate departure from
"serve the view that already exists".

The reasoning is survey items 1 and 4 together. The disclosure audience
is the namespace, plus `system`, plus every namespace granted full
trust — and `provenance` on a login-minted key holds a named person's
subject identifier, their token's `jti` and the claims they satisfied.
Serving it to that audience means every namespace a person is entitled
to can enumerate who logged in and when, and binding a claim such as
`email` in a mapping rule publishes it to each of them. Phase 4's own
comment already states the intended rule — provenance is what an
administrator reads, the key name is what every namespace lister sees —
so this decision implements an invariant the code already asserts
rather than inventing one.

The cost is that this phase cannot simply call `external_view()`; it
needs a projection that takes the caller into account, and the
`external_view()` docstring that says otherwise has to be corrected in
the same step. That is the work, and it is worth it: the alternative
discloses personal data by default, and a disclosure is not something a
later phase can take back.

A reviewer might reasonably argue that `provenance` is exactly what a
namespace owner needs in order to audit their own namespace's
credentials, and that `system`-only makes the audit story worse for the
person actually responsible for the namespace. That is the strongest
counter-argument and it is why this is a back brief gate rather than a
decision taken quietly. The answer this plan gives is that an owner can
already see *that* a federated key exists, its name, its scopes and its
expiry — everything needed to rotate or revoke it — and that the extra
thing `provenance` adds is the identity of a person, which is the one
field whose disclosure is irreversible.

**D3. The detailed shape spells the key name `key_name`, not `name`.**
Survey item 5 found the API already uses `key_name` in the key create
response, the login mint response and the route parameter;
`external_view()` is the only place that says `name`. One spelling, and
it is the one three surfaces already use. The projection renames the
field rather than `external_view()` changing, because
`external_view()`'s `name` is what the base object convention produces
and other object types follow it.

**D4. The detailed listing includes expired keys; the bare-name listing
does not change.** Survey item 7 found the default differs between
accessors, so this has to be chosen rather than inherited.

An expired key is omitted from the bare listing today, and that
omission is precisely what makes a lapse invisible — the operator guide
currently tells an operator to raise `NAMESPACE_KEY_REAP_GRACE` so that
the key's *disappearance* can be caught in time
(`docs/operator_guide/authentication.md:44-48`). A detailed view whose
entries each carry an expiry does not need that trick: an expired key
is self-describing. Serving it is the direct answer to the audit
question the issue was filed about.

The bare-name listing keeps its current behaviour, because changing
which keys appear in a published response shape is the breaking change
D1 exists to avoid.

The alternative — a second parameter to opt into expired keys — was
rejected as fussy for a surface this small, at the cost that a caller
wanting the old filtering in detailed form has to filter client-side on
a field it now has.

**D5. `Namespace.external_view()`'s `keys` field is left alone.** It is
the other bare-name consumer (survey item 3) and reshaping it is the
same breaking change by another door. `sf-client namespace show`
instead makes a second call for the detail, and falls back to
`ns['keys']` when the capability is absent (step 6e).

**D6. No new database accessor, and `NamespaceKeys` is not used here.**
`keys_with_attributes()` already returns what both reads need in one
query. The projection takes the pair and discards the attributes row
except for `expiry`; nothing hands the secret material onward.

`NamespaceKeys` with a state filter is forbidden by survey item 7: a
point read per key on a freely callable listing. A test asserts the
call count so a later edit cannot reintroduce it.

The honest cost of using `keys_with_attributes()` is that the join
selects the hash and the nonce, so secret material travels from
`sf-database` to `sf-api` for a read that does not need it. Adding a
static-only projection accessor would avoid that, and it is proto,
handler, counter and wrapper work (the `add-grpc-service` skill) for no
functional gain on a low-traffic path. Out of scope, recorded as future
work.

**D7. The capability token is `namespace-key-metadata`, one token for
both reads.** They arrive together and a client has no use for one
without the other, which is the test the `human-login` /
`human-login-metadata` pair failed and were therefore split over. The
comment in `API_CAPABILITIES` says what the token promises: that both
reads exist, that the detailed listing includes expired keys, and that
`provenance` is present only for a `system` caller — a client seeing a
response without it must not conclude the key has none.

**D8. The two existing bare-name listings move off `Namespace.keys`.**
That property's docstring says "Nothing which does not authenticate a
request should use it — `external_view()` is the operator visible
shape" (`shakenfist/namespace.py:195-197`), and both listings use it
anyway, purely to read the dict's keys. They move to
`keys_with_attributes()` directly. This changes no response, and it
stops two read paths loading secret material they never look at.

**D9. The client half lands in this phase, in `client-python`.** The
Execution row names both repositories, and the master plan's review
checklist requires a phase's diff to land in the repositories its row
names. Step 6e runs in a `client-python` checkout; nothing else in the
phase touches that repository, and no step touches both.

## Step plan

Steps run in the order given. 6c is a mutation step and must not run
concurrently with anything else in this repository's worktree. 6d and
6e may run in parallel: they are different repositories.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | high | opus | none | Read decisions D1-D8 and survey items 1, 4, 6 and 7 of this plan first. Add a module-level `_key_detail(key, attrs, caller_is_system)` helper in `shakenfist/external_api/auth.py` near `AuthNamespaceKeysEndpoint` (line 637), returning `{'namespace', 'key_name', 'expiry', 'scopes', 'uuid', 'state'}` and adding `'provenance'` **only** when `caller_is_system`. `key_name` not `name` (D3). Never include `attrs.key` or `attrs.nonce`. Add `get` to `AuthNamespaceKeyEndpoint` (line 690) with the decorator stack `delete` uses -- `@requires_namespace_ownership`, `@arg_is_namespace`, `@api_base.log_token_use` -- and **not** the one on `put`, which adds `@api_base.caller_is_admin` and would make the read administrator-only, leaving the projection with nobody to project for. 404 for a name that is not present, the way `put` and `delete` test membership. Add a `detail` boolean argument to `AuthNamespaceKeysEndpoint.get` (line 649): falsy keeps today's exact bare list, truthy returns `_key_detail` per key with `include_expired=True` (D4). Both read `namespace_key.keys_with_attributes(namespace, include_expired=...)` directly — **not** `namespace_from_db.keys`, and **not** `NamespaceKeys`, which does a point read per key (`namespace_key.py:467-472`). Decide `caller_is_system` from `request_namespace() == 'system'`; do not use `caller_is_admin`, which also requires the `cluster-admin` scope and would make the field vanish for an administrator holding a correctly scoped key. Then: move the bare-name listing in `AuthNamespaceKeysEndpoint.get` and in `Namespace.external_view()` (`shakenfist/namespace.py:469-486`) off `.keys` to `keys_with_attributes()` (D8), changing no response; add `'namespace-key-metadata'` to the `auth` family in `shakenfist/external_api/app.py` (the list at line 470) with a comment in the style of the `human-login` entry, saying what D7 says; correct `external_view()`'s docstring (`shakenfist/namespace_key.py:407-408`) so it no longer claims the view is safe for anyone who can see the namespace, naming provenance as the reason and `auth.py`'s minting comment as the rule. Update the `swag_from` blocks for both endpoints. Commit subject: "Serve a namespace key's metadata." |
| 6b | medium | sonnet | none | Unit tests for 6a, in `shakenfist/tests/external_api/`. Follow the structure of `test_login_mint.py` from phase 4. Required cases: the bare listing is byte-identical to before for a namespace with an expired key and two live ones; the detailed listing includes the expired key and each entry carries its expiry; a `system` caller sees `provenance` and a namespace-owning caller does not, asserted on **the same key** so the pair cannot both pass by accident; a caller in a namespace that the target merely trusts also does not see it; the single-key `GET` 404s for an absent name and for a name in another namespace; no response from either read contains the key hash or the nonce, asserted by searching the serialised body for the known hash and nonce values rather than by checking field names; and a call-count test, with several keys configured, asserting `keys_with_attributes` is called once and that no `mariadb.get_state` call is for a namespace key (D6) -- the request decorators read the *namespace's* state on every request, so "not called at all" is not available; assert the total cannot scale with the key count instead. Run `tox -epy3`; totals with `./.tox/shared/bin/stestr last --no-subunit-trace`. Commit subject: "Test the key metadata reads." |
| 6c | medium | sonnet | none | Mutation-test 6a's guards. Write the mutations as a runnable script under this session's scratchpad, named in your report, and **never mutate the checkout in place** — copy the tree, mutate the copy, run each mutant under `timeout 600`, and count a timeout as killed. Required mutants, each of which must be killed by a named test from 6b: drop the `caller_is_system` condition so `provenance` is always included; invert it; change `caller_is_system` to compare against `caller_is_admin` instead; add `attrs.key` to the projection; make `detail` default to truthy; make the detailed listing pass `include_expired=False`; make the single-key `GET` return a key from any namespace. Record the kill table in this plan's Results section, one row per mutant with the test that caught it and the assertion message. A surviving mutant is a finding, not a note: report it rather than adjusting the mutant. Commit subject: "Mutation test the key metadata disclosure rule." |
| 6d | medium | sonnet | none | Documentation. Remove the three interim notes listed in survey item 8 — `docs/operator_guide/authentication.md:40-51` (keep the `NAMESPACE_KEY_REAP_GRACE` forensic-window advice, which stands on its own; remove only the clause that the fields are unreadable and the #3672 pointer), `docs/developer_guide/authentication.md:157-161`, and rewrite the three `get_namespace_keynames` examples in `docs/developer_guide/api_reference/authentication.md` (lines 214, 235, 280) against whichever client methods step 6e adds. Document both reads in the API reference, the `detail` parameter, the capability token, and — in the operator guide, in the section that currently carries the limitation — that `provenance` is a `system`-only field and why, cross-referencing rather than restating the provenance paragraph phase 4 added at `docs/operator_guide/authentication.md`. Do not add a settings row: this phase adds no configuration. Leave `docs/developer_guide/ci_api_coverage.md` alone. Commit subject: "Document reading a key's metadata." |
| 6e | medium | sonnet | `client-python` checkout | **This step runs in `/srv/kasm_profiles/mikal/vscode/src/shakenfist/client-python`, on its own branch and worktree, and produces its own pull request. It must not touch the `shakenfist` repository.** In `shakenfist_client/apiclient.py`, beside `get_namespace_keynames` (line 1616, which stays, unchanged): add `get_namespace_keys(namespace)` calling the collection with `detail=true`, and `get_namespace_key(namespace, key_name)` calling the single-key route. Then `sf-client namespace show` (`shakenfist_client/commandline/namespace.py:66-126`): where it prints the "Key Names:" list from `ns['keys']`, call `get_namespace_keys` when the cluster advertises `namespace-key-metadata` and render a table of name, expiry, scopes — plus provenance when the field is present, which it will be only for a `system` caller. Fall back to the existing bare list when the capability is absent, so the command still works against an older cluster; follow however this client already feature-detects a capability, and if it does not yet, add the check in one place rather than per call site. Keep all three output modes (`json`, `pretty`, and the default machine-readable one) working, and extend `shakenfist_client/tests/test_client_apiclient.py` beside the existing `test_get_namespace_keynames` (line 262). Commit subject: "Read key metadata in namespace show." |

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| The detailed listing discloses a person's identity to a namespace owner, which cannot be undone once a client has read it | D2 restricts `provenance` to `system`. 6b asserts both halves on the same key; 6c mutates the condition three ways and requires a named test to catch each. The management session reproduces at least one of those mutants itself rather than trusting the report |
| `caller_is_system` is derived wrongly and the field vanishes for a real administrator, or appears for a non-administrator | The brief names the predicate to use and the one not to, with the reason. 6c includes a mutant that swaps them |
| The bare-name response changes by accident, breaking three in-tree consumers and any out-of-tree client | 6b's first test asserts byte-identical output for a namespace with a mix of live and expired keys. D5 keeps `Namespace.external_view()` out of scope entirely |
| The listing becomes an N+1 on a freely callable path | D6 forbids `NamespaceKeys`; 6b asserts no `mariadb.get_state` call is for a namespace key, and that the total cannot scale with the key count; 6c mutates the accessor choice |
| Secret material reaches a response | 6b asserts on the serialised body against the known hash and nonce, not on field names, so a renamed field cannot pass |
| The client half drifts from the server shape because they land in two repositories at different times | D7's single capability token gates the client behaviour, and 6e's fallback path is required to work against a cluster without it. The server PR may land first; the client PR may not land before it |
| 6c's mutation run collides with a `pre-commit` run in the same worktree, so a killed mutant is silently restored | 6c runs alone, on a copy, as its brief states |

## Definition of done

Each item is falsifiable, and the command is given where there is one.
Run from the phase worktree unless stated.

* [ ] Phase 4's row in the master plan's Execution table reads
      `Complete` with `666cff3d9a` in `Merged`, and phase 6's reads
      `In progress` with its `Plan` cell linking this file.
      `python3 tools/check-plan-status.py` passes.
* [ ] `GET /auth/namespaces/<ns>/keys` with no query parameter returns
      exactly what it returns on `develop`, for a namespace holding two
      live keys and one expired one. A test asserts the whole response,
      not the presence of individual names.
* [ ] `GET /auth/namespaces/<ns>/keys?detail=true` returns one object
      per key including the expired one, each carrying `key_name`,
      `expiry` and `scopes`.
* [ ] No response from either read contains a key's hash or nonce:
      a test searches the serialised body for the fixture's known hash
      and nonce strings and finds neither.
* [ ] `provenance` appears for a `system` caller and is absent for a
      namespace-owning caller, asserted on the same key in one test
      class, and absent for a caller in a namespace that the target
      trusts.
* [ ] The field is spelled `key_name` everywhere this phase adds it:
      `grep -c "'name':" ` over the new projection returns 0.
* [ ] Neither read performs a point read per key.
      `grep -rn 'NamespaceKeys(' shakenfist/external_api/auth.py`
      returns nothing, and a test asserts that no `mariadb.get_state`
      call is for a namespace key while listing a namespace with several
      keys, and that the total number of such calls cannot scale with the
      key count. The namespace's own state is read on every request by
      `arg_is_namespace` and by token verification, so an assertion that
      `get_state` is never called would be unsatisfiable.
* [ ] `namespace_from_db.keys` is no longer read by either listing:
      `grep -n "\.keys\.get('nonced_keys'" shakenfist/external_api/auth.py`
      returns only the two write-path guards, in the key update and
      key delete handlers, and
      `grep -n "nonced_keys" shakenfist/namespace.py` no longer matches
      inside `external_view`.
* [ ] `external_view()`'s docstring no longer claims the view is safe
      to hand to anyone who can see the namespace:
      `grep -A3 'operator visible view of a key'
      shakenfist/namespace_key.py` does not contain "safe to hand".
* [ ] `namespace-key-metadata` is in the `auth` family with a comment:
      `grep -n 'namespace-key-metadata' shakenfist/external_api/app.py`
      shows both the token and a comment above it.
* [ ] All seven mutations in 6c killed, each by a named test recorded
      in this file's Results section. A surviving mutation is a failed
      Definition of done, not a note.
* [ ] No page says the fields are unreadable. `grep -rn 'not readable
      through any API\|names\* only\|3672' docs/ --include=*.md`
      matches nothing outside `docs/plans/`.
* [ ] No fact about these reads is stated differently on two pages: the
      operator guide, the developer guide and the API reference agree
      on which fields each read serves, on whether expired keys appear,
      and on who sees `provenance`.
* [ ] Issue #3672 is closed by the pull request body, not by hand.
* [ ] `git diff --stat develop... -- ':!docs/plans'` in this repository
      touches only `shakenfist/external_api/auth.py`,
      `shakenfist/external_api/app.py`, `shakenfist/namespace.py`,
      `shakenfist/namespace_key.py`, files under `shakenfist/tests/`,
      and files under `docs/`. No proto, no `mariadb.py`, no migration.
* [ ] The `client-python` diff touches only
      `shakenfist_client/apiclient.py`,
      `shakenfist_client/commandline/namespace.py` and
      `shakenfist_client/tests/`, and the `shakenfist` diff touches
      nothing in `client-python`.
* [ ] `docs/plans/order.yml` is unmodified:
      `git diff --exit-code develop... -- docs/plans/order.yml`.
* [ ] `pre-commit run --all-files` passes in both repositories, and
      `flake8` is clean on each changed file individually — the
      pre-commit hook lints `HEAD~1`, not the working tree.

## Back brief

Four points need Michael's answer before 6a starts. Each is cheap to
ask now and expensive to redo later.

1. **D2, the provenance gate.** `provenance` to `system` only, or to
   any caller who can read the namespace? The counter-argument is in
   D2: a namespace owner auditing their own credentials is the person
   the field was added for, and `system`-only makes their job harder.
   The disclosure is irreversible, which is why this plan chose the
   narrow answer.
2. **D1, the shape.** Both reads — a new single-key `GET` and a
   `detail` parameter on the collection — or only one of them?
3. **D4, expired keys.** Included in the detailed listing, as planned,
   or filtered the way the bare listing filters them?
4. **D9, the client half.** Does step 6e land in this phase, as a
   separate `client-python` pull request, or does it become a
   `client-python` issue the way the claim verbs did
   (client-python#364), leaving this phase server-only?

## Execution notes

**Back brief, 2026-11-15 (AEDT).** All four gates answered as planned:
`provenance` to `system` only (D2), both reads (D1), expired keys
included in the detailed listing (D4), and the client half landing in
this phase as a separate `client-python` pull request (D9). No decision
changed, so no step's brief was rewritten on their account.

**Two corrections to this plan before 6a ran.** Both were found by
checking the step brief's own premises against the tree, not by the
step:

* 6a's brief said the new single-key `GET` should mirror the decorator
  stack on `AuthNamespaceKeyEndpoint.put`. That stack carries
  `@api_base.caller_is_admin`, which would have made the read
  administrator-only and left D2's caller-sensitive projection with
  nothing to decide. The brief now names `delete`'s stack, which is
  also the collection `get`'s: `@requires_namespace_ownership`,
  `@arg_is_namespace`, `@api_base.log_token_use`.
* The Definition of done placed the two surviving `nonced_keys`
  membership guards in the key create and update handlers. `post` has
  no such guard; they are in `put` and `delete`.

**Step 6a, 2026-11-15 (AEDT).** Three departures from the row, all
kept after review:

* **`state` is not in the projection**, though the row asks for it.
  `NamespaceKey.state` reads `mariadb.get_state()` per object
  (`shakenfist/baseobject.py:624-636`) and `keys_with_attributes()`
  builds its keys from static data with no state join, so serving a
  state would cost one point read per key -- which is the N+1 D6
  forbids and the Definition of done asserts does not happen. The
  expiry the view does carry is what an operator was reading the state
  to infer. `uuid` is free and is served.
* **The single-key `GET` includes expired keys**, which the row does
  not say. Otherwise a key the detailed listing shows would 404 on its
  own route.
* **Two registrations the new parameter forces**, both of which live
  in test files although 6a was briefed to write no tests: the
  capability snapshot in
  `shakenfist/tests/external_api/test_root.py`, and a `BOOLEAN_READS`
  row in `shakenfist/tests/external_api/test_boolean_sweep.py`, which
  fails the moment a boolean is declared without one. The sub-agent's
  edit to the second was refused by its own permission settings, so
  the management session wrote it, checked that it is not vacuous --
  the fixture namespace holds one key, so the two arms of `detail`
  produce different responses -- and corrected that group's comment
  from three routes to four.

Two things 6b inherits from this: there is no `state` field to assert
on, and the bare listing no longer reads the clock through
`shakenfist.namespace.time.time`, so a test must set real past and
future expiries rather than mock that attribute.

**Step 6b, 2026-11-18 (AEDT).** 21 tests in
`shakenfist/tests/external_api/test_namespace_key_metadata.py`, all
passing, with the full suite green at 7576 tests.

One required assertion was unsatisfiable as written and the plan has been
corrected at its three mention sites rather than the test weakened to
match. The row, the risk table and the Definition of done all asked for a
test proving `mariadb.get_state` is "not called at all" during a listing.
It is always called: `arg_is_namespace` resolves the namespace through
`_live_namespace` (`shakenfist/external_api/auth.py:98-102`) and token
verification reads `ns.state.value`
(`shakenfist/external_api/base.py:1919-1923`), both to refuse a deleted
namespace, and both read the *namespace*. What D6 actually forbids is a
state read *per key*, so that is what the test asserts: no `get_state`
call carries `ObjectType.NAMESPACE_KEY`, and the total is under four with
nine keys configured, so it cannot scale with the key count. This is a
stronger guard than the original wording, which a per-key read on a
different object type would have satisfied.

The trusted-namespace test incidentally settled a documentation question
6d got wrong: the trustee receives a 200 with `provenance` withheld, not
a refusal, which is the proof that the read's audience is wider than the
namespace's own callers.

**Step 6d, 2026-11-18 (AEDT).** The three interim notes are gone and both
reads are documented in the operator guide, the developer guide and the
API reference, which were re-read together and agree on the fields served,
on expired keys and on who sees `provenance`.

Three corrections were made in review. The step described the reads as
requiring the caller to "own the namespace", in four places across the
three guides. `requires_namespace_ownership` tests
`namespace_is_trusted` (`shakenfist/namespace.py:677-688`), which admits
the namespace itself, `system`, and every namespace in the target's
`trust` list -- the audience survey item 4 sized this phase's disclosure
against, and the reason D2 exists at all. All four now say so, and the
operator guide says plainly that granting trust hands over key metadata,
since that is a decision an operator makes. The step also had to rewrite
the provenance paragraph phase 4 added, which claimed provenance is
"readable by anyone who can list keys in that namespace"; that is false
now and the rewrite was kept.

One claim the step reported as unverified is true: `NAMESPACE_KEY_REAP_GRACE`
set to `0` does retain expired keys forever
(`shakenfist/daemons/cluster/scheduled_tasks.py:872-874`).

**Step 6e, 2026-11-18 (AEDT).** Landed in `client-python` as its own
commit on branch `oidc-authentication-phase-06-client`, per D9.

Review took `provenance` out of the default machine-readable output,
leaving it in the table and the JSON. That output is comma separated and
a serialised object carries commas of its own, and because the field
reaches some callers and not others the column count would have depended
on who was asking. A test holds it out. There is a precedent for a dict
in such a row -- the events listing prints `extra` that way
(`shakenfist_client/commandline/namespace.py:338`) -- but it is a wart
rather than a pattern to extend.

The two halves were checked against each other rather than assumed: the
client sends `?detail=true` as a URL query string and the server binds
that argument with `location='json_or_query'`
(`shakenfist/external_api/auth.py:747`), so a cluster parses it. This was
the one place both repositories could have passed their own tests and
still not worked together.

## Results

### Mutation testing the disclosure rule (step 6c)

Seven mutants, each run on a copy of the tree with bytecode writing
disabled and under `timeout 600`. All seven are killed. Six were killed
by tests written in 6b; the seventh exposed a coverage gap and is
discussed below.

| # | Mutation | Killed by | Assertion |
|---|----------|-----------|-----------|
| 1 | `provenance` included unconditionally | `DetailedListingTestCase.test_an_owner_entry_has_exactly_the_published_fields` | `OWNER_FIELDS != set(entry)` |
| 2 | The caller condition inverted | `ProvenanceDisclosureTestCase.test_system_sees_provenance_and_the_owner_does_not_on_one_key` | `KeyError: 'provenance'` for the system caller |
| 3 | `_caller_is_system()` replaced by `api_base.caller_has_admin_rights()` | `ProvenanceDisclosureTestCase.test_a_scoped_system_caller_still_sees_provenance` | `PROVENANCE != entries['person'].get('provenance')` |
| 4 | `attrs.key` added to the projection | `NoSecretMaterialTestCase.test_no_response_contains_a_hash_or_a_nonce` | `assertNotIn(secret, body)` |
| 5 | `detail` defaulting to truthy | `BareListingTestCase.test_the_bare_listing_is_unchanged_and_omits_the_expired_key` | the bare name list differs from the detailed dicts |
| 6 | The detailed listing passing `include_expired=False` | `DetailedListingTestCase.test_the_detailed_listing_includes_the_expired_key` | `{'login', 'person', 'lapsed'} != set(entries)` |
| 7 | The single-key read searching every namespace | `SingleKeyTestCase.test_a_name_which_exists_only_in_another_namespace_is_a_404` | `404 != 200` |

**Mutant 3 survived its first run, and that was the one finding.** No
test exercised a caller who is in `system` but does not hold
`cluster-admin`, so `request_namespace() == 'system'` and
`caller_has_admin_rights()` agreed on every case and the substitution was
invisible. That is precisely the regression `_caller_is_system`'s
docstring exists to warn about: the field would have vanished for an
operator holding a correctly scoped key, which is a silent loss of a
field rather than an error anyone would notice.

The gap was closed by adding
`test_a_scoped_system_caller_still_sees_provenance`, which mints a key
in `system` scoped to `auth.read` -- enough to satisfy the universal
scope check on these routes, not enough to satisfy `cluster-admin` --
and asserts both reads still serve `provenance`. The test was confirmed
to fail against mutant 3 before being accepted, so it is not vacuous.
Neither the mutant nor 6a's code was adjusted to achieve this.

Mutant 7 also had to be rebuilt once: its first form resolved no
namespaces at all, so every single-key read returned 404 and the mutant
died for the wrong reason. The faithful version, which chains the
accessor over every active namespace the way `auth.py` itself iterates
them, genuinely returns another namespace's key before the test refuses
it.
