# Phase 4: minting the entitled credential set at login

## Context

Phase 4 of [PLAN-oidc-authentication.md](PLAN-oidc-authentication.md).
Planning effort: **high**. Review effort: **high** — the master plan
asks for it by name, on the grounds that this is the cluster's
privilege boundary and the failure mode is silent.

Phase 0 settled the two questions this phase's shape depends on. Human
sessions use **exchange** rather than direct-bearer, so what a person
ends up holding is an ordinary namespace key and the access tokens
minted from it. And a credential stays **one namespace deep**, so the
multiplicity of a person who works in several namespaces is held by
the client as a *set* of single-namespace keys rather than by one
credential naming several.

Phase 3 built the half of a human login that happens before any of
this: discovery, the `client_id` and `oidc_scopes` columns no
discovery document can supply, and `GET /auth/login`, the
unauthenticated endpoint that tells a client which providers this
cluster will accept a person from.

What is missing is the act in the middle. A person completes a flow at
their provider — phase 7's work, in `client-python` — and comes back
holding an identity token. Nothing in this repository turns that token
into the set of credentials the person is entitled to. The targeted
workload exchange, `POST /auth/federated`
(`shakenfist/external_api/auth.py:2419`), requires the caller to name
one namespace and one mapping rule, which a person has no way to know
and which would in any case need one call per namespace.

This phase builds the server half: one unauthenticated request
carrying one identity token, matched against every mapping rule the
cluster holds for that issuer, minting one namespace key per match.

## Scope

**In scope.**

* `POST /auth/login`: validate an identity token, find every mapping
  rule it satisfies, and mint one namespace key per rule.
* A rule accessor that reads every rule's policy for one issuer in a
  bounded number of database round trips rather than two per rule.
* An audience override on `federation.validate_token()`, because the
  audience an identity token carries is not the audience a workload
  token carries (survey finding 1).
* Replay refusal, rate limiting and a cap on the size of the minted
  set, all on an endpoint anybody may call.
* Audit events on each minted key, on each namespace minted into, and
  on each refusal that has an identifiable owner.
* The documentation an operator and an API consumer need, and the
  capability token a client feature-detects this with.

**Out of scope.**

* Both OAuth flows, the credential cache, and choosing which member of
  the set to present per request. All of that is phase 7, in
  `client-python`. Nothing in this phase performs a token request, a
  device authorization request or a PKCE exchange.
* Serving a namespace key's metadata back as a read — that is phase 6
  and issue #3672. This phase returns each credential's expiry and
  scopes *in the mint response*, which is a different thing: it is what
  the caller was just given, not a read of what exists (decision D10).
* Any change to server-side authorisation. `request_namespace()` stays
  a string split (`shakenfist/util/access_tokens.py:83`), and none of
  the fifty-seven sites that read it move. Phase 0 refused that change
  and gave three reasons; this phase does not reopen it.
* Periodic re-discovery, provenance on `jwks_uri`, and the
  `GET /auth/login` TTL cache. All three are master-plan Future work
  entries written by phase 3.
* Any change to what `POST /auth/federated` accepts or returns. The
  workload exchange is not refactored to share an implementation with
  the login path; what they share is `federation.py`, which is where
  the master plan's review checklist says shared validation lives.

## What the survey found

The master plan's phase 4 section makes one falsifiable claim about the
tree — that `request_namespace()` is still a string split at
`shakenfist/util/access_tokens.py:83` — and it is still true. The
mechanism the section describes, "match the identity token's claims
against the mapping rules of each namespace which has one", is
buildable as described. **Nothing in that section is wrong.**

Eight things it does not say, each of which changes what the phase
has to build.

### 1. A human identity token cannot validate against an issuer's configured `audience`

This is the finding that most changes the phase, and it would have
been found by the implementer only after writing a test against a real
provider.

`federation.validate_token()` (`shakenfist/federation.py:294`) reads
`configuration.audience` and passes it to `jwt.decode()` with
`verify_aud` on and `require` naming `aud`. The operator guide
describes that column as "Tokens must be minted for this. Usually your
cluster's API URL" (`docs/operator_guide/authentication.md:163`), and
the worked GitHub Actions example has the workflow request a token
with `audience=https://sf.example.com`
(`docs/operator_guide/authentication.md:470`). For a workload that is
exactly right: the workload asks its provider for a token stamped with
whatever audience we tell it to ask for.

A person cannot. OpenID Connect Core 1.0 §2 requires an ID token's
`aud` to contain the `client_id` of the relying party, and that is
what Keycloak and Authentik stamp. The client identifier is already a
column, added by phase 3, and it is the field that decides whether an
issuer is offered for login at all
(`shakenfist/external_api/auth.py:2778`). So an operator who follows
the operator guide — API URL in `audience`, client identifier in
`client_id` — has configured a cluster where every human login fails
`aud` verification, with a refusal deliberately terse enough to give
them nothing to work from.

Nothing in the master plan, phase 0 or phase 3 mentions this. Open
question 4 settled that the audience is per-issuer rather than
per-cluster and stopped there, because at the time the only tokens
were workload tokens.

### 2. `/auth/federated` is described in the code as the only unauthenticated route with a body

`limit_federated_body_size` (`shakenfist/external_api/app.py:232`)
refuses an oversized body before any reader parses it, and its comment
opens "`/auth/federated` is the only unauthenticated route which takes
a body". The check is a path equality test against that one string.

That comment is true today — `GET /auth/login` takes no body — and
this phase falsifies it. The hook has to cover the new route, or the
endpoint's own size check is the only one, and the endpoint's own
check is documented in the same comment as unable to protect anything:
by the time a `flask_restful` method runs, `log_request` has already
called `get_json(force=True)` and parsed the whole body.

Credential redaction, by contrast, is already inherited.
`handles_credentials()` (`shakenfist/external_api/base.py:111`) tests
`path == '/auth' or path.startswith('/auth/')`, so a body and response
on `/auth/login` are redacted in the request log without any change.

### 3. A rule's issuer lives in its attributes row, so "every rule for this issuer" has no indexed path

`MappingRules` (`shakenfist/mapping_rule.py:606`) can list every rule
on the cluster — `get_all_mapping_rules()`
(`shakenfist/mariadb.py:16118`) when no namespace is given — but what
it yields is static rows: uuid, namespace, name, version. The issuer
the rule accepts, the claims it binds, its scopes, its TTL and its key
name prefix are all in the attributes row, reached through
`policy()` (`shakenfist/mapping_rule.py:474`). Iterating `MappingRules`
and calling `policy()` costs one attributes read per rule, and the
state filter every caller needs
(`AuthNamespaceRulesEndpoint.get`,
`shakenfist/external_api/auth.py:1703`) costs a second read per rule.

Two round trips per rule, on an endpoint an anonymous caller may reach
sixty times a minute per source address, is the shape of amplification
that `enforce_rate_limit`'s own docstring
(`shakenfist/federation.py:462`) warns about for issuer resolution. On
a cluster with two hundred rules it is four hundred round trips per
login attempt.

The tree already contains both halves of the answer, each added for
exactly this reason.

* `find_namespace_keys()` (`shakenfist/mariadb.py:14646`, direct at
  `14082`, gRPC at `14467`) returns each static row joined with its
  attributes row, wrapped by `keys_with_attributes()`
  (`shakenfist/namespace_key.py:425`), whose docstring says the
  alternative "would cost two extra point reads per key on /auth".
* `reap_expired_namespace_keys()`
  (`shakenfist/daemons/cluster/scheduled_tasks.py:842`) resolves
  "which of these objects is in an actionable state" with one
  `get_objects_by_state()` call (`shakenfist/mariadb.py:5085`) rather
  than reading `.state` per object, after the per-object version was
  measured at ~15,200 round trips per pass on sfcbr and filed as issue
  3814.

### 4. `get_objects_by_state()` returns `None` for failure, and the distinction is load bearing here

Its docstring says so explicitly: "or None if the query failed
(distinct from [] which means no matches)". A login that reads `None`
as "no rule is in an actionable state" mints nothing, which is safe. A
login that reads `None` as "do not filter by state" mints from
soft-deleted rules, which is an authorisation bypass: `delete()` on a
mapping rule is a soft delete
(`shakenfist/mapping_rule.py:601`) and the row survives until the
hard-delete reaper gets to it.

### 5. The replay table's second key component is a mapping rule, and a login has no single rule

`_get_federation_replay_table()` (`shakenfist/mariadb.py:2433`) has a
composite primary key on `(token_id, rule_uuid)`, and its docstring
justifies the grain: "Refusal is per (token, rule) rather than per
token because exchanging one identity against two rules to reach two
namespaces is a legitimate pattern, while re-exchanging it against the
same rule is not."

A login is precisely that legitimate pattern, performed in one
request. Claiming the pairs one at a time means a login that mints
three of five and then fails can never be retried to completion: the
retry finds three pairs taken and two free, and returns a set of two
with no indication that it is short. `rule_uuid` is a plain UUID
column with no foreign key, so what goes in it is a matter of meaning
rather than of schema.

### 6. Each mint is a bcrypt hash, and nothing bounds how many a login performs

`NamespaceKey.new()` (`shakenfist/namespace_key.py:202`) hashes the
secret with `hash_secret()` (`shakenfist/namespace_key.py:49`) —
bcrypt at the library default cost — and then writes a static row, an
attributes row, two state transitions and the audit events the caller
adds. `FEDERATION_RATE_LIMIT_PER_MINUTE` defaults to 60
(`shakenfist/config.py:612`) and is counted per source address, so one
address may make sixty login attempts a minute. Multiplied by the
number of rules a single identity happens to match, with no cap, that
is an unbounded amount of deliberately slow work reachable by anybody
holding one valid identity token.

### 7. The two existing predicates for "which issuers count" disagree

`GET /auth/login` lists issuers in `TrustedIssuer.ACTIVE_STATES`,
which is `{STATE_INITIAL, STATE_CREATED}`
(`shakenfist/trusted_issuer.py:62`), and narrows further to issuers
that are login capable: a `client_id`, plus at least one of
`authorization_endpoint` and `device_authorization_endpoint`.

`issuer_claiming_url()` (`shakenfist/federation.py:247`), which is
what `issuer_for_token()` resolves through, accepts any issuer whose
state is not `STATE_DELETED`.

**Corrected during execution of step 4c.** This finding first said
the two sets differ for an issuer in `STATE_ERROR` or
`STATE_DELETE_WAIT`. Neither state exists for a trusted issuer:
`state_targets` (`shakenfist/trusted_issuer.py:67`) admits only
`None -> initial`, `initial -> {created, deleted}` and
`created -> deleted`, because configuring an issuer is atomic and so
its state machine has no error state and nothing between created and
deleted. The two sets really differ in two places. An issuer whose
static row was written but whose state write did not land reads as a
state of `None`, which is not `STATE_DELETED`, so it resolves through
the exchange's predicate while the listing's `ACTIVE_STATES` test
excludes it. And they differ for every issuer configured for workloads
only, which is every issuer in existence today.

So a token from an issuer `GET /auth/login` does not advertise would
otherwise resolve and validate on the login path. Changing
`issuer_claiming_url()` would change the workload exchange, which is
out of scope; the login endpoint has to apply the narrower predicate
itself, and the two predicates then have to be expressed once rather
than twice.

### 8. Minted keys accumulate until two sweeps have run, and this cluster has been bitten by that before

Expiry is enforced at use, so a lapsed key stops authenticating
immediately. Removal is slower:
`reap_expired_namespace_keys()` soft deletes a key only
`NAMESPACE_KEY_REAP_GRACE` after it expired, and
`per_deleted_object_checks` hard deletes it `CLEANER_DELAY` after
that. A person who logs in repeatedly across five namespaces leaves
five keys per login for the whole of that window, and issue 3814's
history is what happens when namespace keys accumulate: the sweep's
cost grew with the backlog rather than with the work.

This is not a reason to make a re-login rotate rather than mint
(decision D8 explains why it must not), but it is a number the
operator documentation should give honestly.

### `file:line` drift in the master plan

Phase 3's survey finding 10 established the policy, and this phase
follows it: the master plan's 39 references were re-resolved at
`6b0ab81b2a` and the plan says so, and re-sweeping them every phase
would spend a commit per phase on churn. The references *this* phase's
implementer will read were checked individually. One, in the phase 4
section itself, still resolves. Four in the management session review
checklist no longer do, and are listed here so the next survey does not
rediscover them as news:

| Cited as | Actually at |
|---|---|
| `shakenfist/external_api/scopes.py:137-142` (`satisfies()`) | `scopes.py:122` |
| `shakenfist/external_api/base.py:279-299` (`caller_is_admin`) | `base.py:306` |
| `shakenfist/external_api/base.py:2637` (`method_decorators`) | `base.py:2899` |
| `shakenfist/namespace.py:494-518` (`get_api_token()`) | `namespace.py:650` |

Every reference in *this* file was resolved against `ccd8f6b701`.

### Corrections made at source

Per the skill's rule that false claims are fixed where they live, the
planning commit also changes:

* The master plan's phase 4 section, which gains survey finding 1 —
  the audience an identity token carries is not the one an issuer's
  `audience` column holds — because an implementer who reads only that
  section would build a login that cannot work against a provider.
* The master plan's Execution table: phase 3 becomes `Complete` with
  `d884dd56c1` in `Merged`, and phase 4's `Plan` cell links this file
  with status `In progress`.
* `docs/plans/index.md`'s row for the plan, which moves to `2 of 11`
  and whose Intent text is rewritten to say what this phase does.
* Phase 3's execution notes, which left the CodeQL `py/full-ssrf`
  alert handed back rather than resolved. It was dismissed on the
  forge as "won't fix" on 2026-11-12, which is what that note said
  the repository's convention would be.

No other reference was touched. A later step must not redo this.

## Decisions

**D1. The endpoint is `POST /auth/login`, beside the `GET` that
advertises it.** The two halves are one surface: a client reads the
`GET` to learn which providers it may start a flow at, and posts the
resulting token to the same path. The alternative — making `namespace`
and `rule` optional on `POST /auth/federated` — would give one
endpoint two authorisation shapes, targeted and set-wide, chosen by
the absence of a field. That is the kind of distinction a later edit
loses. Registering a second method on the existing resource also
inherits `handles_credentials()` redaction (finding 2) and the
`/auth/` swagger family without further argument.

**D2. The login path verifies `aud` against the issuer's `client_id`,
and `federation.validate_token()` grows an audience override to do
it.** Finding 1 is otherwise unanswerable: an operator cannot put both
their cluster's API URL and their client identifier in one `audience`
column, and OpenID Connect gives them no choice about what a provider
stamps in an identity token.

The override is a parameter on the existing function, not a second
copy of validation. The master plan's review checklist forbids a
second copy of JWT validation, JWKS fetching or caching, and it is
right to: the algorithm allowlist
(`shakenfist/federation.py:47`), the zero leeway
(`shakenfist/federation.py:68`) and the `require` list are the
security properties, and they must not come to exist twice.

Consequence, stated so that a reviewer can check it and a test can
assert it: a workload token stamped with the cluster's API URL is
refused by `POST /auth/login`, and a human identity token stamped with
the client identifier is refused by `POST /auth/federated` — unless an
operator has deliberately set `audience` and `client_id` to the same
string, which is their business. Two token shapes, two doors.

The login path does **not** accept either audience. Accepting both
would let a CI workload's token mint the whole entitled set rather
than the one credential its rule names, which is a widening nobody
asked for and which no test would notice.

**D3. Every rule the identity satisfies mints, including a rule owned
by `system`, and nothing else does.** The authorisation decision stays
entirely in mapping rules. This phase adds no second policy beside
them, no allow list of namespaces a login may mint into, and no
special case keyed on a namespace's name.

The master plan asks that this phase "must mint several
single-namespace keys without ever minting one into `system` as a side
effect". Requiring an explicit, claim-matched rule *owned by* `system`
is what satisfies that: there is no path to a `system` key that does
not go through a rule an administrator wrote in the `system`
namespace, and the operator guide already describes such a rule as "a
standing offer of administrative credentials to whoever satisfies its
claims".

**This is the decision a reviewer is most likely to argue with, so
here is the alternative and why it loses.** The alternative is to
exclude `system` from the set-minting login entirely, so that an
administrator who wants a cluster-admin credential must call the
targeted exchange naming `system` and its rule. It has a real
argument: phase 0 refused multi-namespace credentials partly because a
credential naming `system` alongside a tenant namespace would satisfy
`caller_is_admin` on every request, and a *set* containing a `system`
key puts an administrative credential in the client's cache for the
whole of its TTL, where a client bug that picks the wrong member
escalates.

It loses on three counts. It would make the one endpoint humans use
the one endpoint that silently ignores a rule the operator wrote,
which is a worse failure than the one it prevents — the operator sees
their rule listed, matching, and doing nothing. It does not actually
remove the credential from the client's cache, because the targeted
exchange is still there and phase 7 will use it. And it puts a
namespace name in the authorisation path, which is exactly the
coupling phase 0's answer to open question 5 was pleased to avoid.

What the phase does instead is make the mint loud rather than
forbidden: a `system` mint gets its own audit event wording, and the
operator documentation says in terms that a mapping rule in `system`
turns a group membership at the identity provider into cluster
administration.

**D4. The token is claimed once for the whole login, keyed on the
issuer rather than on a rule.** Finding 5 is the reason: per-rule
claiming makes a retry after a partial failure return a silently short
set, which is the worst available outcome because the client cannot
tell. One claim, taken before any key is minted, makes a retry a clean
refusal the person can act on — ask the provider for another token.

The claim goes in the existing table with the issuer's uuid in the
`rule_uuid` column, and that column's docstring is extended to say
that its second component is "what the token was spent on": a mapping
rule for a targeted exchange, an issuer for a login. No migration, and
no new table for one more meaning of the same claim.

A consequence worth writing down: minting is not transactional, so a
failure part way through leaves the person with a partial set and a
spent token. That is the same trade `POST /auth/federated` already
makes and documents — "the right way round to be wrong", because the
alternative is a window in which one token mints twice — and the fix
is the same: ask the provider for another token.

**D5. A login that matches no rule is refused with 401 and the same
terse category as every other refusal.** Not a 200 carrying an empty
list. A client that received `200 []` would have to treat a successful
login with no credentials as a distinct state, and the honest reading
of it — "you are authenticated and entitled to nothing" — is not
distinguishable by the client from "this cluster has not finished
being configured".

401 rather than 403 for consistency with the exchange, which answers
401 to every refusal including a claim mismatch, and because a
different code for "token good, no entitlements" would turn the
endpoint into an oracle for whether a given identity matches any rule
at all.

**D6. The size of the minted set is capped by configuration, and
exceeding the cap refuses the whole login.** Finding 6 is the reason
for a cap; the refusal rather than a truncation is because a truncated
credential set is one the person cannot tell is incomplete, and they
would discover it as a permission denied in whichever namespace fell
off the end.

`FEDERATION_MAX_LOGIN_CREDENTIALS`, defaulting to 16. The number is
chosen against the only evidence there is: phase 0 step 0c found the
CI conductor holding eight keys, in eight namespaces, which is the
largest real credential set anybody has. Sixteen is twice that and
still bounds the work a single valid token can command at sixteen
bcrypt hashes.

The refusal is `507`. The request is well formed, the token is good,
and the caller can do nothing about the answer; what is wrong is that
the cluster's rules and its cap disagree. It is not transient, so 503
would be a lie, and it is not the caller's input, so the 400 family
misdescribes it.

This plan first chose 409 and the back brief asked about it. The
answer came from this API's own documented taxonomy, which the
planning survey had not read: the comment above `CLAIM_REFUSAL_STATUS`
(`shakenfist/external_api/auth.py:1937`) reserves 409 for a request
that "conflicts with the durable state of a resource", where "the
caller has to change something ... before it can succeed", and gives
507 to capacity exhaustion, where "no change to the request will help
until something is released". A person who matches more rules than the
cap allows cannot change anything, which makes 409 a misdescription by
this repository's own rule and 507 the code its neighbours already
use for instance scheduling and address exhaustion. The refusal is
logged with the counts and the setting name, and the response names
neither.

**D7. The rule set is read in two queries, not two per rule.** Add a
joined accessor filtered by issuer in SQL, mirroring
`find_namespace_keys()`, plus one `get_objects_by_state()` call to
drop rules that are not in an actionable state, mirroring
`reap_expired_namespace_keys()`. Both precedents exist because the
per-object version was measured and found wanting (finding 3).

`None` from `get_objects_by_state()` refuses the login. Finding 4
explains why the alternative is an authorisation bypass rather than a
performance regression.

**D8. A key's name carries a random discriminator, exactly as the
exchange's does, and never the person's identity.** `NamespaceKey.new()`
rotates an existing key of the same name — new hash, new nonce, same
uuid — so a deterministic name derived from the subject would mean
that logging in on a laptop invalidates the credential a session on a
workstation is still using. The random discriminator is what makes a
second login additive.

The person is identified in `provenance`, where the exchange already
puts `sub`, `jti` and the satisfied claims, and not in the key's name,
which is visible to everyone who can list the namespace's keys.
`provenance['source']` is `'login'` rather than `'federated'`, so an
operator can tell a human's credential from a workload's without
reading the rest of it.

**D9. The login budget is its own, keyed `login-mint:<source>`.** Phase
3 separated the metadata endpoint's budget from the exchange's, and
gave the reason: the two cost very different amounts and a shared
counter means a burst of one throttles the other. A login mint is the
most expensive of the three — a JWKS fetch and up to sixteen bcrypt
hashes — so it gets the third budget rather than sharing the
metadata endpoint's. The prefix is eleven characters, which leaves the
64 character column room for the longest IPv6 address.

**D10. The response carries each credential's `expiry` and `scopes`
alongside its secret.** Phase 7 cannot write a credential cache
without knowing when each member expires, and the endpoint that serves
a key's metadata as a read is phase 6, which is not built. Returning
them here costs nothing and discloses nothing: the caller has just
been handed the secret those two facts describe. The alternative —
phase 7 inferring expiry from the rule's `key_ttl`, which it cannot
see — is how a cache comes to outlive the credential it holds.

**D11. A refusal is evented where there is an owner to tell, and
logged where there is not.** The same rule `_federated_refusal()`
(`shakenfist/external_api/auth.py:2379`) already applies, for the same
reason: `/auth/login` is unauthenticated, and eventing an anonymous
caller's refusals into a namespace's audit log would hand them a way
to write unbounded rows there. A login has no single owning rule, so
an untrusted issuer, a rejected token or a cap refusal is logged
only. A per-rule claim mismatch is not evented either, because a
login evaluates every rule and a person entitled to one namespace
would otherwise write a near-miss event into every other namespace on
the cluster on every login.

That last one is a deliberate loss of signal and is worth being
explicit about: the probing a namespace owner would want to see is
visible on the targeted exchange, which is where a caller naming their
rule is doing something a stranger would not.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | high | opus | none | **Read every mapping rule for one issuer in two queries.** Commit subject: `Read mapping rules joined with their policy.` Add a joined accessor for mapping rules, mirroring the namespace-key one exactly: `_direct_find_mapping_rules(issuer)` beside `_direct_find_namespace_keys` (`shakenfist/mariadb.py:14082`), `_grpc_find_mapping_rules` beside `_grpc_find_namespace_keys` (`shakenfist/mariadb.py:14467`), and the public `find_mapping_rules(issuer=None)` beside `find_namespace_keys` (`shakenfist/mariadb.py:14646`). The direct path selects the static table joined to the attributes table on `uuid` and pushes the issuer filter into SQL as `attrs_table.c.issuer == issuer`; with `issuer=None` it returns every rule, which is what the rule-listing endpoint and the reaper would want. Return `(static, attributes)` pairs the way `find_namespace_keys` does. The proto work follows the `add-grpc-service` skill in `.claude/skills/`: a request and response message in `protos/database.proto`, `tox -egenprotos` to regenerate (never hand-edit the generated descriptor line), a `FindMappingRules` handler in `shakenfist/daemons/database/main.py` beside `GetAllMappingRules` (line 4502), and the operation name registered in the Monitor counters list (line 6707) — the existing `get_mapping_rules_in_namespace` entry is in that list and is the pattern. Then add `rules_with_attributes(issuer=None)` to `shakenfist/mapping_rule.py`, mirroring `keys_with_attributes()` (`shakenfist/namespace_key.py:425`) including the docstring's explanation of why the joined read exists, and returning `list[tuple[MappingRule, MappingRuleAttributesData]]` built with `MappingRule.from_static_data()`. **Unlike `keys_with_attributes()`, this one must say in its docstring that it does not filter on object state and that every caller therefore has to**, because a mapping rule's only delete is a soft delete (`shakenfist/mapping_rule.py:601`) and a soft-deleted rule that still mints is an authorisation bypass. Do not add the state filter inside the accessor: step 4c resolves it with one `get_objects_by_state()` call for the whole set, which is the pattern `reap_expired_namespace_keys()` (`shakenfist/daemons/cluster/scheduled_tasks.py:842`) established and whose comment explains why per-object state reads were removed. Unit tests: the joined read returns a rule's policy without a second read; the issuer filter excludes another issuer's rule in SQL rather than in Python; `issuer=None` returns every rule; a rule whose attributes row is missing does not appear (the join drops it) and that is asserted rather than assumed; and the gRPC and direct paths agree. Do not change any existing caller of `get_all_mapping_rules()` or `get_mapping_rules_in_namespace()` in this step. |
| 4b | high | opus | none | **Teach `federation.py` to validate a human token and find the rules it satisfies.** Commit subject: `Match an identity against every rule it satisfies.` Two changes in `shakenfist/federation.py`, both Flask-free — this module has no endpoint of its own and that is deliberate, as its header comment says. First, add an `audience` keyword to `validate_token()` (line 294) which overrides `configuration.audience` when given, defaulting to `None` meaning "use the issuer's". Decision D2 is the why, and the docstring must carry it: an OpenID Connect identity token's `aud` is the relying party's `client_id`, a workload's is whatever the workload asked for, and one column cannot hold both. Change nothing else about the decode — the algorithm allowlist, `LEEWAY_SECONDS`, `verify_iat: False` and the `require` list are the security properties and they stay. Second, add `matching_rules(claims, issuer_name, rules)` which takes the claims of an already-validated token, the issuer's name, and the `(rule, attributes)` pairs from `rules_with_attributes()`, and returns the subset whose policy names this issuer and whose bound claims the token satisfies, each paired with the satisfied claims `match_claims()` (line 378) returned. It must call `match_claims()` rather than reimplementing matching: claim comparison stays exact, an exact string or membership of a list of exact strings, with no globbing, prefix matching or coercion, and the master plan's review checklist says so. A rule whose policy is damaged or whose `scopes` or `key_ttl` is empty is skipped rather than raising, since one damaged rule must not refuse a login the cluster's other rules would have granted — count the skips and return the count so the caller can log it. `ClaimMismatch` from `match_claims()` is an ordinary non-match here, not an error. Return the matches in a deterministic order, sorted by namespace then rule name, so that a capped or partially-failed login is reproducible and a test can assert the whole list. Unit tests: a rule for another issuer is not matched; a non-matching rule is skipped and a matching one beside it is still returned; the satisfied claims are the token's values and not the rule's matchers; a rule with no scopes is skipped and counted; a rule with an empty `bound_claims` is skipped and counted, because `match_claims()` refuses to treat that as matching every identity and that refusal must not become a login failure; the order is namespace then name; and `validate_token()` with an audience override accepts a token whose `aud` is the override and refuses one whose `aud` is the issuer's configured audience. |
| 4c | xhigh | opus | none | **Mint the entitled set.** Commit subject: `Mint the entitled credential set at login.` Add a `post` to `AuthLoginEndpoint` (`shakenfist/external_api/auth.py:2691`), `@api_base.public`, taking one body field `token`. Read `AuthFederatedEndpoint.post` (`shakenfist/external_api/auth.py:2445`) first and follow its order, which its own comment says is a security property rather than a style: each step cheaper than the next, and the expensive ones behind checks an anonymous caller cannot pass by guessing. The order here is: content-length backstop against `FEDERATION_MAX_TOKEN_BYTES`; argument check; `federation.enforce_rate_limit('login-mint:%s' % source)` answering 429 with `suppress_traceback=True` (D9); `federation.issuer_for_token()` for the cheap untrusted-issuer refusal with no network; then the login-capable predicate from finding 7 — the issuer must be in `TrustedIssuer.ACTIVE_STATES` and have a `client_id`, which `GET /auth/login` already tests at `shakenfist/external_api/auth.py:2778`, so **extract that predicate into one module-level helper and call it from both** rather than writing the test twice; then `federation.validate_token(token, issuer, audience=attrs.client_id)` (D2), catching `JWKSTrustAnchorUnusable` as a 503 and `TokenValidationFailed` as a refusal exactly as the exchange does; then `mapping_rule.rules_with_attributes(issuer=issuer.name)` and one `mariadb.get_objects_by_state(ObjectType.MAPPING_RULE, sorted(MappingRule.ACTIVE_STATES))` to drop rules that are not actionable, refusing the login if that call returns `None` (D7, finding 4 — this is the one place where reading the failure as an empty answer would be an authorisation bypass, so the test for it is not optional); then `federation.matching_rules()`; then the no-match refusal (D5); then the cap (D6); then one `federation.refuse_replay()`-shaped claim for the whole login keyed on the issuer's uuid (D4) — extend `record_federated_exchange`'s and `_get_federation_replay_table()`'s docstrings (`shakenfist/mariadb.py:23795`, `shakenfist/mariadb.py:2433`) to say the second key component is what the token was spent on, a rule or an issuer; then mint. Each mint mirrors the exchange's: `'%s-%s' % (prefix, sf_random.random_id()[:8])` for the name (D8), `credentials.generate()` for the secret, `NamespaceKey.new()` with the rule's scopes and `time.time() + key_ttl`, and `provenance` carrying `source: 'login'`, the rule uuid and name, the issuer, the satisfied claims, `jti` and `sub`. Verify the namespace is live before minting into it, as the exchange does. Audit events on the minted key and on the namespace, and **distinct wording for a mint into `system`** so that an administrative credential is legible in the audit log as one (D3). Return a list of objects carrying `namespace`, `key_name`, `key`, `expiry` and `scopes` (D10), with a module-level example string beside `federated_example` (`shakenfist/external_api/auth.py:2371`) and a full `swag_from` response list: 200, 400, 401, 413, 429, 503, 507. Do not catch `DatabaseUnavailable`: `handle_database_unavailable` is already in `Resource.method_decorators` and that is why the exchange does not catch it either. Then three registrations: extend `limit_federated_body_size` (`shakenfist/external_api/app.py:232`) to cover this route and rewrite its comment, which currently says `/auth/federated` is the only unauthenticated route with a body (finding 2); add `FEDERATION_MAX_LOGIN_CREDENTIALS` to `shakenfist/config.py` beside the other `FEDERATION_*` settings with `ge=1` and a description saying what it bounds and why (the two phase 3 settings at the end of that group are the pattern); and add `human-login` to the `auth` family of `API_CAPABILITIES` (`shakenfist/external_api/app.py:456`) with a comment in its neighbours' style saying what a client learns from it, distinct from `human-login-metadata` because a client can see the issuer list on a server that cannot mint a set. Unit tests, at least: a token matching rules in three namespaces mints three keys and returns three entries; the response's key set per entry is asserted exactly; a token matching nothing is 401; a soft-deleted rule does not mint; `get_objects_by_state()` returning `None` refuses rather than minting; a second presentation of the same token is refused and mints nothing; a token whose `aud` is the issuer's `audience` rather than its `client_id` is refused; an issuer with no `client_id` is refused even though `issuer_claiming_url()` resolves it; exceeding the cap mints nothing and answers 507; a rule in `system` that matches does mint, and a cluster with no `system` rule never mints into `system`; the rate limiter is consulted before the issuer is resolved; the body size hook refuses an oversized body on this path; and a cluster with no trusted issuers configured behaves exactly as it does today. |
| 4d | high | opus | none | **Prove the guards.** Commit subject: `Mutation test the login mint.` Use the `mutation-check` skill and `~/bin/mutation-check` with a JSON mutant spec, on a *copy* of the tree — never mutate the checkout in place, restore from the copy and never with `git checkout <dir>`, and run each mutant under `timeout`, counting a timeout as killed. At least these eight, each of which must be killed by a *named* test recorded in this file's Results section: the audience override falls back to `configuration.audience`; the login-capable predicate is dropped so any resolvable issuer may be logged in through; `get_objects_by_state()` returning `None` is treated as an empty list; the state filter is dropped so soft-deleted rules mint; the cap comparison is `>` instead of `>=` (or whichever direction the implementation uses — mutate it to the off-by-one); the replay claim is moved to after the first mint; `matching_rules()` returns every rule for the issuer rather than only the matching ones; and the `system` audit wording is dropped. A mutant that survives is a missing test, and a mutant killed by a test whose failure message names something else is also a finding. Record the spec's path and the kill-by-test table in Results. |
| 4e | medium | sonnet | none | **Document the login.** Commit subject: `Document minting the credential set at login.` Four files, and every statement checked against the code as it stands after 4c rather than against this plan. In `docs/operator_guide/authentication.md`: a section on human login as an administrative act, covering that a person's entitlements are the mapping rules their claims satisfy, one namespace key per rule; that **a mapping rule in the `system` namespace turns a group membership at the identity provider into cluster administration**, cross-referencing the existing sentence about a standing offer of administrative credentials rather than restating it; that the `audience` column and the `client_id` column are stamped by different things and why human login verifies against the latter (finding 1) — this is the single most useful paragraph in the change, because an operator who has set `audience` to their API URL and cannot log in has no other way to find out; the cap and its refusal; and the key accumulation window from finding 8, giving `NAMESPACE_KEY_REAP_GRACE` and `CLEANER_DELAY` by name. Add `FEDERATION_MAX_LOGIN_CREDENTIALS` to the `### Settings` table in the same file, in the style of its neighbours, including the `FEDERATION_RATE_LIMIT_PER_MINUTE: 0` caveat that applies to every unauthenticated endpoint in this family. In `docs/developer_guide/authentication.md`, add the login path to the federated-identity description, saying that validation is the same function with a different audience and that there is still exactly one copy of it. In `docs/developer_guide/api_reference/authentication.md`, document `POST /auth/login`: unauthenticated, rate limited on its own budget, body and response shapes, and every response code including the 507. In `docs/glossary.md`, add or extend an entry for the credential set if the glossary's style supports one. Do not add anything to `AGENTS.md` or `ARCHITECTURE.md`: no convention and no component boundary changed. Do not add a `README.md` bullet. |

Steps run in order. 4b depends on 4a's accessor existing, 4c on both, 4d on 4c, and 4e documents what 4c actually did rather than what this plan says it will. None of them may run in parallel: they share one worktree, and `pre-commit`'s stash is repository-wide.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| An operator configures human login, follows the operator guide for `audience`, and every login fails with a deliberately uninformative 401. | D2 makes it impossible by validating against `client_id`. 4e's operator paragraph covers the operator who already has an issuer configured. The management session checks 4c's test that a token stamped with the issuer's `audience` is refused — that test is the one that proves which value is in force. |
| A mapping rule in `system` quietly becomes a cluster-administration grant to anyone in a group. | Not prevented, by decision: D3 argues the alternative is worse. Made legible instead — distinct audit wording on the key and the namespace, and an operator-guide paragraph that says it in terms. The management session checks both exist and that a cluster with no `system` rule never mints into `system`. |
| A soft-deleted rule mints, because the state filter was dropped or because `get_objects_by_state()` failed and the failure was read as an empty answer. | 4a's accessor docstring states that it does not filter state and that callers must. 4c tests both the filter and the `None` case. 4d mutates both and records which test kills each. |
| One valid identity token commands an unbounded amount of bcrypt work. | D6's cap, D9's own rate-limit budget, and a test that exceeding the cap mints nothing. The cap's default is argued from phase 0's measurement rather than chosen. |
| A retried login returns a short set the client cannot tell is short. | D4's single claim for the whole login, taken before any mint. 4d mutates the claim to after the first mint and requires a test to catch it. |
| Keys accumulate and feed the sweep cost issue 3814 described. | Not fixed here — D8 explains why a re-login must be additive. Documented honestly in 4e with both grace periods named, so an operator sizing a cluster can see it. Worth a Future work entry in the master plan if 4c's review finds the window longer than this plan assumes. |
| The new gRPC method and proto regeneration conflict with another phase in flight. | 4a regenerates with `tox -egenprotos` and never hand-edits the serialised descriptor; phase 3 hit exactly this and resolved it by regenerating. If `develop` moves under the branch, rebase and regenerate rather than merging the stub. |
| 4e documents the plan rather than the implementation. | Its brief says so explicitly and names the check. The management session re-derives two statements from the code, not from the report — phase 3's experience is that documentation steps are where an intention gets written down as a fact. |

## Definition of done

Each item is falsifiable, and the command is given where there is one.
Run from the phase worktree unless stated.

* [ ] Phase 3's row in the master plan's Execution table reads
      `Complete` with `d884dd56c1` in `Merged`, and phase 4's reads
      `In progress` with its `Plan` cell linking this file.
      `docs/plans/index.md`'s row for the plan reads `2 of 11`.
      `python3 tools/check-plan-status.py` passes.
* [ ] `POST /auth/login` mints one key per matching rule, and a unit
      test asserts the exact key set of each response entry rather
      than the presence of individual keys.
* [ ] There is exactly one copy of JWT validation. `grep -c 'jwt.decode'
      shakenfist/federation.py` returns 2 — the unverified-issuer read
      and the verified decode — and `grep -rn 'jwt.decode'
      shakenfist/external_api/` returns nothing.
* [ ] The login path verifies `aud` against `client_id`. There is a
      test which presents a token stamped with the issuer's configured
      `audience` and asserts a refusal, and a test which presents one
      stamped with the `client_id` and asserts success. Both assert on
      the same issuer configuration, so the pair cannot both pass by
      accident.
* [ ] A soft-deleted mapping rule mints nothing, and
      `get_objects_by_state()` returning `None` refuses the login
      rather than minting. Two tests, named for those two cases.
* [ ] The login-capable predicate exists once.
      `grep -c 'client_id' shakenfist/external_api/auth.py` counts no
      occurrence inside `AuthLoginEndpoint.post`, because the method
      calls the shared helper instead.
* [ ] A second presentation of one identity token mints nothing and is
      refused, whatever it matched the first time. The test asserts
      the key count in each namespace is unchanged by the second call.
* [ ] A cluster with no mapping rule in `system` never mints a key in
      `system`, proven by a test which matches rules in two other
      namespaces and asserts `system` has no new key.
* [ ] A cluster with no trusted issuers configured behaves exactly as
      it does today, proven by tests which pre-date this change still
      passing unmodified.
* [ ] One login costs a bounded number of database round trips
      independent of the number of rules on the cluster: the rule read
      is one `find_mapping_rules` call plus one
      `get_objects_by_state` call. A test asserts the call count with
      a mock, against a cluster with several rules, so that a future
      edit reintroducing a per-rule read fails here.
* [ ] `FEDERATION_MAX_LOGIN_CREDENTIALS` refuses rather than
      truncates: a test with the cap set below the number of matching
      rules asserts a 507 **and** that no key was minted in any
      namespace.
* [ ] `limit_federated_body_size` covers `/auth/login`, and its
      comment no longer claims `/auth/federated` is the only
      unauthenticated route with a body.
* [ ] `human-login` is in the `auth` family of `API_CAPABILITIES` with
      a comment: `grep -n 'human-login' shakenfist/external_api/app.py`
      shows both it and `human-login-metadata`.
* [ ] All eight mutations in 4d killed, each by a named test recorded
      in this file's Results section. A surviving mutation is a failed
      Definition of done, not a note.
* [ ] Nothing in the diff performs an OIDC token request, device
      authorization request or PKCE exchange — that is phase 7, in
      another repository. Falsifiably: nothing outbound is sent
      anywhere except a JWKS endpoint, so `grep -rn 'session.post\|
      requests.post\|requests.get' shakenfist/external_api/auth.py
      shakenfist/federation.py` returns nothing.
* [ ] No fact about human login is stated differently on two pages.
      Specifically: the operator guide, the developer guide and the API
      reference agree on which audience a human token is verified
      against, on whether the set is capped, and on what happens when
      no rule matches.
* [ ] `git diff --stat develop... -- ':!docs/plans'` touches only
      `protos/database.proto`, files under `shakenfist/protos/`,
      `shakenfist/mariadb.py`, `shakenfist/mapping_rule.py`,
      `shakenfist/federation.py`, `shakenfist/config.py`,
      `shakenfist/external_api/auth.py`,
      `shakenfist/external_api/app.py`,
      `shakenfist/daemons/database/main.py`, files under
      `shakenfist/tests/`, and files under `docs/`.
* [ ] `docs/plans/order.yml` is unmodified:
      `git diff --exit-code develop... -- docs/plans/order.yml`.
* [ ] `pre-commit run --all-files` passes, and `flake8` is clean on
      each changed file individually — the pre-commit hook lints
      `HEAD~1`, not the working tree.

## Back brief

Three things need Michael's answer before 4c runs, and one before 4a
does. The first is the one that matters.

1. **D3: does a matching mapping rule in `system` mint at login?**
   The plan says yes, on the argument that the alternative makes the
   one endpoint humans use the one that ignores a rule the operator
   wrote. The alternative — exclude `system` from the set and require
   a targeted exchange for an administrative credential — is a
   defensible reading of the master plan's "never minting one into
   `system` as a side effect", and it is cheap to build either way.
   Expensive to change afterwards, because it is a documented
   authorisation property by the time 4e has run.
2. **D6: is `409` the right refusal for exceeding the cap, and is 16
   the right default?** The code is a judgement call between a wrong
   family and a lie; the default is argued from a sample of one.
3. **D5: 401 for a token that matches no rule, or something a client
   can distinguish?** The plan chooses consistency with the exchange
   and non-disclosure over a client's ability to tell "nothing for
   you" from "bad token". A person seeing "login refused" when their
   administrator has simply not written their rule yet is the cost.
4. **4a's proto change touches generated stubs.** Phase 3 hit a
   generated-descriptor conflict on `develop` and resolved it by
   regenerating. If another phase is adding a gRPC method right now,
   4a should be sequenced against it rather than discovered in a
   rebase.

Nothing in this phase touches a live system, and no step needs a
secret or a token.

## Execution notes

**Back brief answered 2026-11-13, 15:20 AEDT.** All four points
settled before any step ran.

* **D3 — a matching rule owned by `system` mints.** Confirmed as
  planned, at the planning gate on 2026-11-12.
* **D6 — the cap's refusal is `507`, not the `409` this plan first
  chose.** This is the one decision the back brief changed, and the
  argument is now recorded in D6 itself. The short version: this API
  already documents what its refusal codes mean, above
  `CLAIM_REFUSAL_STATUS` (`shakenfist/external_api/auth.py:1937`), and
  by that taxonomy 409 promises the caller something they can change.
  A person who matches more rules than the cap allows cannot. The
  planning survey had not read that comment, which is why the plan
  argued the question from first principles and reached a different
  answer from the repository's own.
* **D6 — the default stays 16**, twice the largest real credential set
  phase 0 step 0c measured.
* **D5 — a token matching no rule is refused 401**, terse, as planned.
* **4a's proto sequencing — no sequencing needed.** Checked rather than
  asked. Two branches in flight touch `protos/database.proto`:
  `agent-operation-dependencies-phase-02-dispatch-evaluation` adds
  `dependency_wait_started` to `AgentOperationAttributesProto`, and
  `scheduler-reservations-phase-05a-hard-ceiling` edits comments in
  `AdmitInstancePlacementReply`. Neither touches the mapping-rule
  messages or the service's RPC list, so 4a's addition cannot conflict
  textually. The serialised descriptor in `shakenfist/protos/` will
  conflict with whichever of the three lands first; the resolution is
  the one phase 3 used, rebase and regenerate with `tox -egenprotos`,
  never a hand-edit.

**Steps ran in the planned order**, 4a then 4b then 4c, each committed
after review before the next began. They share one worktree and
`pre-commit`'s stash is repository-wide, so running two at once would
have let one silently revert the other.

**Corrections the management session made to a step's work.**

* 4b wrote the audience override's fallback as `audience or
  configuration.audience`. The client identifier column has no
  minimum length (`shakenfist/schema/trusted_issuer_attributes.py:92`
  declares it `Optional[str]` with `max_length` only), so an empty
  string is storable, and `or` would have read it as "use the
  issuer's audience" — verifying a person's identity token against
  the audience a *workload's* token carries, which is the one
  widening D2 forbids. Changed to an explicit `is None` test, so an
  empty value fails the completeness check instead, and pinned with
  a test that was confirmed to fail against the original.

**Facts this phase's execution proved wrong, corrected at source.**

* **Survey finding 7** claimed the listing's and the exchange's
  issuer predicates differ for an issuer in `STATE_ERROR` or
  `STATE_DELETE_WAIT`. A trusted issuer has neither state. Found by
  4c, corrected in the finding itself, and the endpoint's comment
  describes the real difference: a static row whose state write did
  not land reads as `None`, which is not `STATE_DELETED`.

**Design choices taken during execution that the step table left
open, and kept.**

* The shared login-capable predicate returns the *audience* rather
  than a boolean (`login_audience()`). The mint then cannot reach
  `validate_token()` with an unchecked column at all, which is a
  stronger guarantee than remembering to check first, and it is why
  `client_id` appears nowhere inside `AuthLoginEndpoint.post` as the
  definition of done requires.
* `get_objects_by_state()` returning `None` answers **503**, not a
  401. D7 required only that it refuse; 503 is the honest code,
  because the failure is ours and the token is still good. It is
  taken before the replay claim, so the token is not spent and the
  retry can succeed.
* The body-size hook tests membership of a two-path frozenset rather
  than a `/auth/` prefix. `POST /auth` sends a namespace key and the
  rest of the family is authenticated with bodies this limit is not
  sized for, so a prefix test would have applied a JWT-sized cap to
  routes that are not JWTs.
* A match whose namespace has been deleted is skipped and logged
  rather than refusing the whole login, for the same reason a rule
  that can grant nothing is skipped. If that empties the set the
  login is refused rather than answered with `200 []`.
* `refuse_replay()`'s body was factored into a private
  `_claim_token_use()` shared with the new
  `refuse_replay_for_login()`. The public function's signature and
  refusal message are unchanged.

## Results

### Step 4d: the guards are enforced by tests

Twelve mutants, twelve killed, none survived and none killed only by
the wrong tests. The eight the step table names, plus four variants:
the off-by-one in the other direction, the empty-override fallback the
management session corrected in 4b, the issuer state test beside the
login-capable predicate, and the replay claim removed rather than
moved.

The spec is JSON at `mutants.json` in this session's scratchpad,
`/srv/nobackups/claude-tmp/claude-1000/-srv-kasm-profiles-mikal-vscode-src-shakenfist-shakenfist/6f19212c-1716-46e1-9230-cf0f2fb17c2c/scratchpad/step-4d/`,
which the scratchpad reaper will eventually take; it is reproducible
from the table below, each row of which is one exact-string
replacement. Nothing was mutated in the checkout: the tree was copied
to `tree/` beside the spec and `~/bin/mutation-check` ran there, with
a 300 second timeout per mutant against a baseline of 10.2s. The test
command was both modules the change touches, run by the copy's own
interpreter with bytecode writing off:

```
./.tox/shared/bin/python -B -m stestr run --no-subunit-trace \
    'shakenfist\.tests\.(test_federation|external_api\.test_login_mint)'
```

Each mutant was then re-applied on its own and run against only the
test named below, so that the kill is the named test's and the failure
message is the one recorded.

| # | Mutation | Killed by | Failure message |
|---|---|---|---|
| 1 | `validate_token()`'s `expected_audience` becomes `configuration.audience`, so the override is ignored | `test_federation.AudienceOverrideTestCase.test_the_issuers_audience_is_refused_when_overridden` | `AssertionError: TokenValidationFailed not raised` |
| 1b | the explicit `is None` test becomes `audience or configuration.audience`, so an empty override falls back | `test_federation.AudienceOverrideTestCase.test_an_empty_override_refuses_rather_than_falling_back` | `AssertionError: TokenValidationFailed not raised` |
| 2 | the `if not audience` refusal after `login_audience()` is dropped | `test_login_mint.LoginCapableIssuerTestCase.test_an_issuer_with_no_client_id_is_refused` | `MismatchError: 401 != 200` |
| 2b | the `TrustedIssuer.ACTIVE_STATES` test beside it is dropped | `test_login_mint.LoginCapableIssuerTestCase.test_an_issuer_with_no_state_row_is_refused` | `MismatchError: 401 != 200` |
| 3 | `get_objects_by_state()` returning `None` becomes `[]` and the 503 is unreachable | `test_login_mint.DeletedRuleTestCase.test_an_unreadable_state_query_refuses_rather_than_mints` | `MismatchError: 503 != 401` |
| 4 | the `str(rule.uuid) in actionable` filter is dropped from the rule list | `test_login_mint.DeletedRuleTestCase.test_a_soft_deleted_rule_mints_nothing` | `MismatchError: ['dev', 'ops'] != ['ci', 'dev', 'ops']` |
| 5 | the cap test `len(matches) > cap` becomes `>=` | `test_login_mint.CredentialCapTestCase.test_a_login_at_the_cap_still_mints` | `MismatchError: 3 != 2` |
| 5b | the cap test becomes `> cap + 1` | `test_login_mint.CredentialCapTestCase.test_one_more_rule_than_the_cap_refuses` | `MismatchError: 507 != 200` |
| 6 | the replay claim moves inside the mint loop, guarded by `if out:`, so it is taken after the first mint | `test_login_mint.ReplayRefusalTestCase.test_the_claim_is_taken_before_anything_is_minted` | `AssertionError: Expected 'new' to not have been called. Called 1 times.` |
| 6b | the replay claim is removed altogether | `test_login_mint.ReplayRefusalTestCase.test_the_same_token_twice_is_refused` | `MismatchError: 401 != 200` |
| 7 | `matching_rules()` answers `satisfied = {}` on `ClaimMismatch` instead of skipping, so every rule for the issuer matches | `test_federation.MatchingRulesTestCase.test_a_non_matching_rule_does_not_hide_a_matching_one` | `MismatchError: ['ryll'] != ['absent-claim', 'another-repo', 'ryll']` |
| 8 | the `rule.namespace == 'system'` branch is dropped, so both audit wordings are the ordinary ones | `test_login_mint.SystemNamespaceTestCase.test_a_system_mint_is_audited_as_an_administrative_one` | `administrative key minted by human login` replaced by a fourth `key minted by human login` |

Two things worth recording beyond the verdicts.

**No mutant needed a new test.** Nothing survived, so no test file
changed, and the only change in this step's commit is this section.

**Every kill names the thing that broke.** Each message above is from
a run of that test alone against that mutant, and each asserts the
property the mutant removed rather than a side effect elsewhere.
Mutants 1, 6 and 7 also break 20 to 35 other tests in the two
modules, which is collateral from a widened audience, a self-refusing
login and a universally matching rule set; the named test fails in
every case and is the one whose message is on the point.

One `expect` did not fire, which is correct rather than a gap.
`test_an_issuer_with_an_empty_client_id_is_refused` passes under
mutant 1b, because `login_audience()` turns an empty `client_id` into
`None` and the endpoint refuses before validation is reached. The
fallback that mutant breaks is pinned by the federation unit test
instead, which is exactly the division of labour `validate_token()`'s
docstring describes: the empty-string refusal is a backstop, and the
check belongs in the caller.

After the run, `git status --short` in the worktree was empty and
`diff -r` between the worktree's `shakenfist/` and the mutated copy's
reported no differences, so no mutation reached the checkout.
