# Phase 3: OIDC discovery

## Context

This is phase 3 of
[`PLAN-oidc-authentication.md`](PLAN-oidc-authentication.md), and
the first phase of that plan to change production code. Phases 1
and 2 were superseded by `PLAN-auth-federation.md` phase 3, and
phase 0 was a decisions phase whose output was documentation.

Phase 0 landed as pull request #4831 (merge commit `2bbe472eff`,
2026-11-07) and its close-out is the first commit of this phase,
per the `plan-phase-landing` shared block.

What phase 3 exists for is narrow and load-bearing. Auth
federation phase 3 built the *verification* half of an OIDC
relying party: a workload arrives already holding a minted token,
so nothing in the tree ever needed to start a conversation with an
issuer. A human does. Both of the flows open question 9 is
weighing — device code, and authorisation code with PKCE — begin
by calling an endpoint at the identity provider, and a relying
party is supposed to learn where those endpoints are from the
provider's discovery document. Nothing in the tree fetches one.

So phase 3 is the prerequisite of open question 9, and therefore
of phase 7, and therefore of the whole human-login half of the
master plan.

**Planning effort:** high. This phase adds a schema migration, an
outbound network fetch, a new unauthenticated endpoint and a
security-critical equality check. The master plan's Agent guidance
reserves high effort for exactly that list.

**Review effort:** the master plan specifies none for this phase.
Review at high effort anyway, for the reason in the risks section:
the `issuer` equality check is the kind of thing that is easy to
write loosely and expensive to have written loosely.

## Scope

**In scope.**

* Fetching and validating an issuer's OpenID Connect discovery
  document, and storing the three endpoints a client flow needs
  (`authorization_endpoint`, `token_endpoint`,
  `device_authorization_endpoint`) on the `TrustedIssuer`.
* Making `jwks_uri` optional on issuer create and update, so that
  an operator can configure a provider by its issuer URL alone.
* The two fields a flow needs that no discovery document can
  supply: the client identifier registered at the provider, and
  the OIDC scope list to request.
* An administrative way to re-run discovery on an existing
  issuer.
* A public endpoint serving the login metadata a client needs
  before it holds any Shaken Fist credential. See decision D7 for
  why this is here rather than in phase 7.
* Unit test coverage, documentation, and API capability
  advertisement.

**Out of scope, deliberately.**

* **Any client-side flow.** `sf-client` is in the separate
  `client-python` repository; this repository ships only `sf-ctl`
  and `sf-backup` (`pyproject.toml:190-191`). Device code and
  authorisation code with PKCE are phase 7 and cannot be
  implemented in this checkout.
* **Minting credentials at login.** That is phase 4.
* **Functional coverage against a real identity provider.** That
  is phase 9, which stands up a containerised provider. Phase 3
  ships unit tests against a stub document; the only honest test
  of discovery against Keycloak and Authentik is phase 9's.
* **Worked operator walkthroughs for Keycloak and Authentik.**
  Phase 8. Phase 3 documents the fields and the mechanics, not an
  end-to-end provider setup.
* **Taking the JWKS location from a token.** Not up for
  discussion, and nothing here changes it: discovery writes an
  issuer's fields at configuration time and validation keeps
  reading them from the object, exactly as today.
* **Re-running discovery automatically.** See D3.
* **Tightening `issuer_url` to https.** A real gap found by this
  survey, recorded as finding 6 and filed rather than fixed. See
  D10.

## What the survey found

The master plan's phase 3 section was written on 2026-08-12 and
re-checked in phase 0 on 2026-11-07. Its premises were verified
again against `08c26e343b` while planning this phase, and **every
claim phase 3 rests on still holds.** That is a real result and
worth saying plainly. The findings below are the detail the
section does not carry, plus one gap it does not mention.

### 1. Discovery is still genuinely absent

`grep -rn "openid-configuration\|well_known\|device_authorization\|token_endpoint" --include='*.py'`
across the tree returns one hit, and it is a test about guest
image names. There is no discovery code to extend, so this phase
is additive rather than a refactor.

### 2. `jwks_uri` is mandatory, and the API says so three times

`TrustedIssuerAttributesData` declares it non-optional
(`shakenfist/schema/trusted_issuer_attributes.py:50`);
`_validate_issuer_arguments` refuses a create or update without it
and requires https on it
(`shakenfist/external_api/auth.py:895-920`); and
`TrustedIssuer.update()` writes all three attribute columns
together with a field mask naming all three, on the stated
reasoning that a new `issuer_url` with a stale `jwks_uri` is a
broken issuer rather than a partly updated one
(`shakenfist/trusted_issuer.py:253-273`). Making the field
optional therefore touches the schema, the endpoint validator and
the update path, and D4 has to say what the update path writes
when discovery supplies some fields and the operator others.

### 3. The attributes table is at version 1 and has never migrated

`TRUSTED_ISSUER_ATTRIBUTES_VERSION = 1`
(`shakenfist/mariadb.py:482`), and
`_ensure_trusted_issuer_attributes_schema`
(`shakenfist/mariadb.py:14827-14848`) has only the
create-at-version-1 branch — there is no migration step to copy.
The pattern to follow is `_add_missing_node_metrics_columns`
(`shakenfist/mariadb.py:2234-2252`), which takes each column's
type from the table definition so the DDL cannot drift from the
Pydantic model, and guards each `ADD COLUMN` with an existence
check so re-runs are safe.

The object's own version does not move. `TrustedIssuer` has
`initial_version = 1` and `current_version = 1`
(`shakenfist/trusted_issuer.py:41-44`), and that version describes
the *static* row — `uuid`, `name`, `version`. New mutable
attributes do not change its shape, so the dbo version stays at 1
and only the table version becomes 2.

### 4. There is no `client_id` anywhere, and the master plan does not mention it

This is the gap. A device-code flow posts to
`device_authorization_endpoint` with a `client_id`, and an
authorisation-code flow needs one too. Discovery cannot supply it:
it is a registration the operator performs at the provider. The
same is true of the OIDC scope list — a group claim does not
appear in a token unless the client asked for the scope that
carries it.

So "discovery populates a `TrustedIssuer`'s fields" is necessary
and not sufficient. Three of the five things a flow needs are
discovered; two are operator-supplied. The master plan's phase 3
section is silent on both, and this phase adds them, because a
phase 7 in another repository cannot add a column to a table in
this one. The master plan's section has been corrected to say so
as part of this phase's planning commit.

### 5. Admin-only issuer endpoints, and a public endpoint convention to copy

`/auth/issuers` and `/auth/issuers/<issuer_name>` are both
`@api_base.caller_is_admin`
(`shakenfist/external_api/auth.py:960-1095`, routed at
`shakenfist/external_api/app.py:650-652`). A person who has not
logged in yet cannot read either, which is what forces D7.

The convention for an endpoint that must be readable without a
credential is `@api_base.public` (`base.py:2634`), used by `Root`,
`Livez`, `Readyz` (`app.py:567-598`) and by
`AuthFederatedEndpoint` (`auth.py:1902-1906`), whose comment is
the precedent: "Unauthenticated by nature: the whole point is that
the caller has no Shaken Fist credential yet". That endpoint also
shows the shape of abuse resistance for a public auth route —
`limit_federated_body_size` as a before-request hook
(`app.py:232-242`) and `federation.enforce_rate_limit`
(`federation.py:462-489`) counted before anything expensive.

### 6. `issuer_url` is not required to be https

`_validate_issuer_arguments` requires https on `jwks_uri` and says
why — "a JWKS fetched over HTTP can be substituted by anyone on
the path, which turns signature verification into theatre"
(`shakenfist/external_api/auth.py:915-919`) — but applies no such
check to `issuer_url`. Today that is only a bad idea, because
`issuer_url` is compared and never fetched. Discovery fetches it,
so the same reasoning applies with the same force.

Tightening the existing field would be a breaking change to a live
API for deployments this session cannot inspect, so it is filed
rather than fixed (D10). The discovery path enforces https on what
it fetches regardless, which is the part that matters here.

### 7. The CA bundle union is a trap that `requests` will walk straight into

`jwks_ssl_context()` (`federation.py:71-118`) exists because
`ssl.create_default_context(cafile=...)` loads that file *instead
of* the system store, so the short way of writing it would mean
that configuring a private Authentik silently stopped GitHub
tokens verifying. `requests` has the identical trap:
`verify='/path/bundle.pem'` replaces the system store rather than
adding to it. `requests` is a dependency
(`pyproject.toml:125`) and is used for outbound fetches elsewhere
(`shakenfist/images.py:467`), but no code in the tree mounts an
`HTTPAdapter` carrying an `SSLContext`. D8 settles how the fetch
is built.

`gunicorn_config.py:148-164` already calls `jwks_ssl_context()` at
worker start so that a bad bundle path is a log line at deploy
time rather than a failed login weeks later. Discovery inherits
that warning for free by reusing the same function, and must not
introduce a second bundle setting.

### 8. Redirects are refused on the JWKS fetch, and operators have been bitten

`docs/operator_guide/authentication.md:366-380` documents that
PyJWT 2.14 builds its fetch opener with redirects refused, that
`jwks_uri` must therefore be the final URL, and that the symptom
is indistinguishable from an unreachable provider. Discovery
should behave the same way for the same reason, and because an
operator who has read that section will expect it to.

### 9. Capabilities are advertised, and a client cannot feature-detect otherwise

`API_CAPABILITIES` (`shakenfist/external_api/app.py:441-541`)
carries an `auth` family which already advertises
`trusted-issuers`, `mapping-rules` and `federated-exchange`
separately, with comments saying why each is separate. The
`namespace-claims` comment states the obligation plainly: the
server-side advertisement is owed whether or not a client has
caught up. Phase 3 owes two tokens, and phase 7 in another
repository is how they get consumed.

### 10. The whole master plan's `file:line` references have drifted four days

Phase 0 step 0e re-resolved all 39 of them at `6b0ab81b2a` and the
master plan says so. Running phase 0's reference verifier at
`08c26e343b` shows nine now landing on the wrong line, including
`app.py:638-640` for the issuer routes (now 650-652) and
`auth.py:591-595` for `AuthNamespaceKeysEndpoint.get`. Every one
of them was correct at `6b0ab81b2a` — checked with `git show` —
so this is ordinary code movement, not a phase 0 error.

They are **not** swept again here. A master plan that re-resolves
39 references at the top of every phase spends a commit per phase
on churn, and the plan already tells the reader which commit they
were true at. The sections this phase's implementer will read
were checked individually and carry no stale reference: open
question 9's two
(`shakenfist/schema/trusted_issuer_attributes.py:48-50` and
`pyproject.toml:190-191`) still resolve, and the phase 3 and
phase 7 narratives cite no `file:line` at all. Every reference
this phase file cites was resolved against `08c26e343b`. The nine
stale ones are listed here so the next phase's survey does not
rediscover them as news.

### Corrections made at source

Per the skill's rule that false claims are fixed where they live,
the planning commit also changes the master plan:

* The phase 3 section gains the `client_id` and OIDC-scope gap
  from finding 4, and a sentence that the login-metadata endpoint
  lands in this phase.
* The phase 3 row's `Plan` cell links this file; its `Status`
  becomes `In progress`.
* Phase 0's row becomes `Complete` with `2bbe472eff` in `Merged`.
* `docs/plans/index.md`'s row for the plan keeps `In progress`,
  moves to `1 of 11`, and its Intent text is rewritten to say
  what this phase does. The index carries one row per master
  plan and no phase rows, so there is no phase link to add
  there.

No other reference was touched. A later step must not redo this.

## Decisions

**D1. The discovery URL is built by appending to the issuer URL's
path, not by inserting before it.** OpenID Connect Discovery 1.0
§4.1 appends `/.well-known/openid-configuration` after the
issuer's path; RFC 8414 §3.1 inserts it between the host and the
path. The two differ for any issuer with a path, which is both of
the providers phase 8 must document: Keycloak serves
`https://host/realms/<realm>/.well-known/openid-configuration` for
issuer `https://host/realms/<realm>`, and Authentik serves
`https://host/application/o/<slug>/.well-known/openid-configuration`
for issuer `https://host/application/o/<slug>/`. Append is what
works against real providers, so append is what we do.

**D1a. One trailing slash is stripped before appending.**
Authentik's issuer carries a trailing slash and its `iss` claim
carries it too, so an operator will configure it with the slash —
correctly, because D2 compares exactly. Naive concatenation then
produces `.../<slug>//.well-known/openid-configuration`, which
many servers answer with a 404 that looks like "this provider does
not support discovery". Exactly one trailing slash is stripped
from the URL used for the fetch, and the configured value is left
untouched for comparison. This needs a unit test of its own; see
the Definition of done.

**D2. The document's `issuer` member must equal the configured
`issuer_url` byte for byte, or discovery is refused.** No
normalisation, no trailing-slash tolerance, no case folding. This
is the security property of discovery, and it is the mirror of the
exact `iss` comparison the operator guide already explains at
`docs/operator_guide/authentication.md:168-172`: without it, a
document fetched from one place can name endpoints belonging to
another, and we would hand a user's browser to whoever controls
the document.

The comparison is against the value the operator configured, not
against the slash-stripped URL D1a fetched. An operator who
configures Authentik without the trailing slash gets a clean
refusal naming both values, which is the correct outcome: their
`iss` comparison was going to fail at exchange time anyway, and
discovery finding it at configuration time is the whole benefit.

**D3. Discovery runs only when an administrator asks. Never on
the request path, never on a timer.** Three places trigger it:
issuer create, issuer update, and an explicit refresh. Nothing
else.

The reasoning is open question 8's, applied to a smaller case:
putting the identity provider on a path that cluster operation
depends on is a reliability regression sold as a security
improvement. It is also what the master plan already promises —
"discovery populates a `TrustedIssuer`'s fields at configuration
time, and validation keeps reading them from the object."

The cost is that an endpoint which moves at the provider stays
stale until an administrator re-runs discovery. That is not a
regression: `jwks_uri` is operator-supplied and equally static
today, and an endpoint that moves silently is a provider
migration, which is an administrative event anyway. A periodic
refresher is recorded as future work.

**D4. `jwks_uri` becomes optional, and when supplied it wins.**
Three cases, and the asymmetry is deliberate:

* **`jwks_uri` omitted.** Discovery is mandatory. If it fails, the
  create or update fails, with the discovery error in the
  response. An issuer with no `jwks_uri` cannot verify a signature
  at all, so a half-created one would be worse than none.
* **`jwks_uri` supplied.** It is written verbatim and discovery
  never overwrites it. Discovery still runs for the other
  endpoints, but it is **best effort**: a failure is logged, an
  audit event is added to the issuer, and the create or update
  succeeds.
* **Both, and they disagree.** The operator's value wins, and the
  disagreement is an audit event on the issuer naming both URLs.
  Not a refusal, because
  `docs/operator_guide/authentication.md:366-380` documents a case
  where the correct `jwks_uri` is deliberately *not* the one the
  document names: a provider that answers its published JWKS path
  with a redirect to a CDN needs the CDN path configured, and
  refusing that would break a deployment the documentation tells
  operators to build.

The best-effort half is what keeps every existing deployment
working. Every issuer configured today supplies `jwks_uri`, so no
existing create or update changes outcome — it only gains fields
and, where the provider publishes no document, an audit event.

**D5. `client_id` and `oidc_scopes` are operator-supplied, and
both are optional.** They cannot be discovered (finding 4). They
are optional because the workload exchange does not need them: an
issuer configured purely to verify GitHub Actions tokens has no
client registration and never will, and requiring one would break
every issuer in existence to serve a flow that issuer will never
run. An issuer is **login-capable** when it has a `client_id` and
a `device_authorization_endpoint` or an `authorization_endpoint`;
only login-capable issuers appear in D7's listing.

`oidc_scopes` defaults to `openid` when a `client_id` is set and
no scopes are given, because `openid` is the one scope every OIDC
flow must request.

**D6. Flow availability is read off the endpoints, and
`grant_types_supported` is not stored.** A provider that supports
the device-code grant publishes `device_authorization_endpoint`
(RFC 8628 §4), so its presence is the signal, and a client that
finds it absent falls back to authorisation code. Storing
`grant_types_supported` as well would mean a JSON column, a second
source of truth about the same question, and a decision about what
to do when they disagree.

The limitation is real and worth writing down: a provider can
publish the endpoint and not have enabled that grant for *our*
client, in which case the flow fails at the provider with the
provider's own error message. That is a configuration mistake the
operator can read, which is a better failure than a client
silently not offering a flow that would have worked.

**D7. A public `GET /auth/login` serves login metadata, and it
lands in this phase.** This is the decision most likely to be
argued with, so here is the argument.

The master plan puts the client flows in phase 7, in
`client-python`. A person running `sf-client login` holds no
Shaken Fist credential — that is the point — and `/auth/issuers`
is admin-only (finding 5). So the metadata has to reach them from
an unauthenticated endpoint, and an unauthenticated endpoint in
this repository cannot be added by a phase that executes in
another one. Leaving it out would make phase 3 unable to satisfy
the dependency the master plan says it exists to satisfy: "neither
flow can begin without the issuer's endpoints".

It serves, per login-capable issuer: `name`, `issuer_url`,
`client_id`, `oidc_scopes`, and whichever of
`authorization_endpoint`, `token_endpoint` and
`device_authorization_endpoint` are set. Nothing else — not
`jwks_uri`, not `audience`, not the uuid, not the state, and not
issuers that are not login-capable.

What it discloses is which identity provider a cluster trusts for
human login. That is mild: the endpoints themselves are public at
the provider, and a client-visible login configuration is what
every OIDC relying party publishes. It is not nothing either —
it tells an attacker where to aim a phishing page — so the
narrowness of the field list above is part of the decision, and
the endpoint is rate limited like `/auth/federated`.

**D8. The fetch is a `requests` session with an `HTTPAdapter`
carrying `jwks_ssl_context()`, redirects refused, body capped.**
Four parts, each for a reason the survey found:

* **The same SSL context as the JWKS fetch**, via an adapter
  rather than `verify=`, because `verify=` replaces the system
  trust store instead of adding to it (finding 7). Reusing
  `jwks_ssl_context()` also means no second CA-bundle setting and
  the existing gunicorn worker-start warning covers discovery too.
* **Redirects refused**, matching the JWKS fetch and the
  documented expectation it created (finding 8).
* **A response body cap**, because nothing else bounds what a
  hostile or broken provider returns and the document is a few
  kilobytes. A new setting,
  `FEDERATION_DISCOVERY_MAX_BYTES`, default 65536.
* **Its own timeout**,
  `FEDERATION_DISCOVERY_FETCH_TIMEOUT_SECONDS`, default the same
  as `FEDERATION_JWKS_FETCH_TIMEOUT_SECONDS`. A separate setting
  rather than a reuse, because that setting's own comment ties its
  value to the request-path hazard of pinning a gunicorn worker
  per queued anonymous request, and discovery runs on an
  admin-only path where that hazard does not exist. One setting
  would mean an operator who lengthens the admin timeout
  lengthens the anonymous one too.

**D9. The scope field is named `oidc_scopes`, not `scopes`.**
Phase 0's survey finding 8 recorded that "claim" already means two
things in this codebase. `scopes` already means a Shaken Fist
authorisation scope — `cluster-admin`, `artifact.*` — on namespace
keys, on mapping rules and in access tokens. An OIDC scope is a
different thing in a different namespace of meaning, and reusing
the word would make `rule.scopes` and `issuer.scopes` look like
the same concept in the same subsystem. The prefix is ugly and the
collision would be worse.

**D10. `issuer_url` is not tightened to https in this phase.**
Finding 6 is a real gap and the correct fix is to require https on
the field. That is a breaking change to a live admin API for
deployments this session cannot inspect, which makes it a decision
for Michael and not for a phase whose job is discovery. It is
filed as an issue in step 3h and recorded in the master plan's
Future work. The discovery fetch enforces https on the URL it
actually requests regardless, so nothing in this phase fetches
plaintext.

## Step plan

Steps 3a to 3d are sequential: each depends on the one before.
Steps 3e, 3f and 3h may run at any time. Step 3g runs after 3b
and 3c.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | **Add the discovery columns to `trusted_issuer_attributes`.** Commit subject: `Add discovery columns to trusted issuers.` Add six optional fields to `TrustedIssuerAttributesData` in `shakenfist/schema/trusted_issuer_attributes.py`: `authorization_endpoint`, `token_endpoint`, `device_authorization_endpoint` and `client_id` as `Optional[str]` with `Field(max_length=1024)` matching the existing three; `oidc_scopes` as `Optional[str]` with `Field(max_length=1024)` (a space-separated OIDC scope string, which is the wire form — do not model it as a list even though the sibling `NamespaceKeyAttributesData.scopes` (`shakenfist/schema/namespace_key_attributes.py:78`) is `Optional[list[str]]`: the client passes this value through verbatim and an operator pastes what their provider's documentation tells them to request, so a join and a split on both sides would buy nothing. Comment that reasoning in the field, and read decision D9 for why it is not called `scopes`); and `discovered_at` as `Optional[float] = None` holding epoch seconds, mirroring `NamespaceKeyAttributesData.expiry` (`shakenfist/schema/namespace_key_attributes.py:75`), which is the closest sibling — a nullable time on an attributes table — and is a float, not an int. Make `jwks_uri` `Optional[str]`, keeping its `max_length` and its comment, and extend the comment to say that it may now be filled from discovery (see D4). Document every new field in the class docstring in the style of the existing four. Then migrate the table: bump `TRUSTED_ISSUER_ATTRIBUTES_VERSION` to 2 at `shakenfist/mariadb.py:482` and add an `if current_ver == 1:` branch to `_ensure_trusted_issuer_attributes_schema` (`shakenfist/mariadb.py:14827-14848`). Write the branch by mirroring `_add_missing_node_metrics_columns` (`shakenfist/mariadb.py:2234-2252`) exactly: take each column's type from the SQLAlchemy table definition with `.type.compile(dialect=sa.dialects.mysql.dialect())` so the DDL cannot drift from the Pydantic model, and guard each `ADD COLUMN` with a `get_table_columns()` existence check so a re-run from either version is safe. The `jwks_uri` column also has to become nullable — a `MODIFY COLUMN` in the same branch, with the type again taken from the table definition. Do **not** touch `TrustedIssuer.initial_version` or `current_version` in `shakenfist/trusted_issuer.py`: those describe the static row (`uuid`, `name`, `version`) and this change adds no static values; survey finding 3 explains it. Add a row for each new column to the `trusted_issuer_attributes` entry in `docs/operator_guide/database.md:1413`. Add unit tests that a version-1 table migrates to version 2 with all six columns and a nullable `jwks_uri`, and that the migration is idempotent. Change nothing in `shakenfist/external_api/`. |
| 3b | high | opus | none | **Write the discovery fetcher.** Commit subject: `Fetch an issuer's OIDC discovery document.` New module `shakenfist/oidc_discovery.py`, deliberately Flask-free and with no endpoint of its own, in the style of `shakenfist/federation.py` — read that file's header comment first, and match its density of explanatory comment: this repository's convention is that security-relevant ordering is written down in the code. Two public functions. `discovery_url(issuer_url)` builds the URL per decision D1: strip exactly one trailing slash, then append `/.well-known/openid-configuration` — OpenID Connect Discovery 1.0 §4.1 append semantics, *not* RFC 8414's insert-before-path, because Keycloak and Authentik serve the appended form. `fetch(issuer_url)` performs the fetch and returns a validated dict, raising a new `exceptions.OIDCDiscoveryFailed` with a message naming what failed. Build the request per decision D8: a `requests.Session` with an `HTTPAdapter` subclass whose `init_poolmanager` passes `ssl_context=federation.jwks_ssl_context()` — do **not** use `verify=<path>`, because that *replaces* the system trust store rather than adding to it, which is the exact trap `jwks_ssl_context()`'s docstring (`shakenfist/federation.py:71-118`) exists to document; `allow_redirects=False`, matching the JWKS fetch and the operator-guide section at `docs/operator_guide/authentication.md:366-380`; `stream=True` with a read capped at `config.FEDERATION_DISCOVERY_MAX_BYTES`; and `timeout=config.FEDERATION_DISCOVERY_FETCH_TIMEOUT_SECONDS`. Add both settings to the federation group in `shakenfist/config.py` next to `FEDERATION_JWKS_FETCH_TIMEOUT_SECONDS` (around line 540), defaults 65536 and 5, which is what `FEDERATION_JWKS_FETCH_TIMEOUT_SECONDS` currently defaults to (`shakenfist/config.py:540`), each with a `description` saying what it bounds and why it is separate from the JWKS one. Refuse, in this order, before parsing anything expensive: a non-https `issuer_url`; a non-2xx status; a redirect status; a body over the cap; a body that is not JSON; a document whose `issuer` member is absent or is not byte-for-byte equal to the `issuer_url` *as passed in*, not the slash-stripped fetch URL (decision D2 — this is the security property of the whole module, so comment it as such); a document whose `jwks_uri` is absent or not https. Return only the members we store: `issuer`, `jwks_uri`, `authorization_endpoint`, `token_endpoint`, `device_authorization_endpoint`, each validated as https when present and absent-is-fine for the last three. Let `exceptions.JWKSTrustAnchorUnusable` propagate — `jwks_ssl_context()` raises it for an unloadable bundle and the endpoints already handle it. Add `OIDCDiscoveryFailed` to `shakenfist/exceptions.py` as a subclass of `FederationException` (`shakenfist/exceptions.py:284`), whose docstring explains that it is subclassed rather than flattened so the exchange endpoint can decide what to tell an unauthenticated caller. Unit tests in `shakenfist/tests/`: the Authentik trailing-slash case from D1a (issuer `https://h/application/o/s/` must fetch `https://h/application/o/s/.well-known/openid-configuration`, one slash not two), the Keycloak path case, an `issuer` mismatch refused, a trailing-slash-only mismatch refused with both values in the message, http refused, a redirect refused, an oversized body refused, a non-JSON body refused, a missing `jwks_uri` refused, and a well-formed document accepted with the endpoints returned. Mock at the session level; make no network calls in tests. Touch no endpoint and no schema. |
| 3c | high | opus | none | **Wire discovery into the issuer endpoints.** Commit subject: `Discover issuer endpoints when configuring one.` In `shakenfist/external_api/auth.py`, change `_validate_issuer_arguments` (lines 895-920) so `jwks_uri` is no longer required, keeping the https check for when it *is* supplied, and keeping `issuer_url` and `audience` mandatory. Add validation for the new body fields: `client_id` and `oidc_scopes` as optional strings capped at 1024 characters, and a `discover` boolean defaulting to `True`. Add the three new operator-supplied/discovered sets to `AuthIssuersEndpoint.post` (lines 998-1020) and `AuthIssuerEndpoint.put` (lines 1056-1087), implementing decision D4 exactly: with `jwks_uri` omitted, discovery is mandatory and a failure returns `502` with the discovery error (add it to the `swag_from` response list); with `jwks_uri` supplied, discovery is best effort and a failure adds an `EVENT_TYPE_AUDIT` event to the issuer, logs a warning and still succeeds; where both are present and differ, the operator's value is written and the disagreement is an `EVENT_TYPE_AUDIT` event naming both URLs — read D4's third bullet for why this is not a refusal. With `discover: false`, skip the fetch entirely, in which case `jwks_uri` is required again. Extend `TrustedIssuer.new()` (`shakenfist/trusted_issuer.py:152-209`) and `TrustedIssuer.update()` (lines 253-273) to carry the six new attribute fields; `update()`'s field mask currently names all three columns on the stated reasoning that they move together, so extend that docstring to say which of the new fields move with them and which an operator can change alone — `client_id` and `oidc_scopes` are independent of a discovery run and must be updatable without one. Add properties and extend `external_view()` (lines 275-287) to serve the new fields, keeping the single `configuration()` read rather than adding per-field reads, for the reason that method's docstring gives. Add a `POST /auth/issuers/<issuer_name>/discover` resource — `caller_is_admin`, `log_token_use`, `scope_family = 'issuer'` — which re-runs discovery on an existing issuer, writes what it finds, adds an audit event and returns the issuer's `external_view()`; register it in `shakenfist/external_api/app.py` beside the two existing issuer routes at lines 650-652. Call `federation.JWKS_CACHE.forget(issuer.uuid)` whenever a write changes `jwks_uri`, since a stale `PyJWKClient` would otherwise keep serving the old key set; note that `_client_and_lock` already replaces a client whose `uri` changed, so this is belt and braces and should be commented as such. Add `issuer-discovery` to the `auth` family of `API_CAPABILITIES` (`shakenfist/external_api/app.py:456-483`), with a comment in the style of its neighbours saying what a client learns from it. Keep every write to `issuer_url` inside `_issuer_url_lock()` as the existing code does. `oidc_discovery.fetch()` returns an `issuer` member alongside the endpoints; ignore it. There is no column for it and it equals the configured `issuer_url` by construction, because the fetch refuses any document where it does not. Unit tests for each of D4's three cases, for `discover: false` requiring `jwks_uri`, for the refresh endpoint, and for an unchanged workload-style create that supplies `jwks_uri` against a provider with no document — which must still succeed, because that is every issuer configured today. |
| 3d | high | opus | none | **Serve login metadata to callers with no credential.** Commit subject: `Serve login metadata for human sessions.` Add an `AuthLoginEndpoint` to `shakenfist/external_api/auth.py` serving `GET /auth/login`, decorated `@api_base.public`. Read `AuthFederatedEndpoint` (`shakenfist/external_api/auth.py:1902-2157`, which runs to the end of the file) first: its comment on why it is unauthenticated is the precedent, and its abuse resistance is the pattern. Return a list of login-capable issuers — those in `TrustedIssuer.ACTIVE_STATES` which have a `client_id` and at least one of `device_authorization_endpoint` or `authorization_endpoint` (decision D5). For each, serve exactly `name`, `issuer_url`, `client_id`, `oidc_scopes`, and whichever of `authorization_endpoint`, `token_endpoint` and `device_authorization_endpoint` are set, and **nothing else** — not `jwks_uri`, not `audience`, not `uuid`, not `state`. Decision D7 explains why the field list is this narrow and should be summarised in a comment on the method. Do not reuse `external_view()`, which serves the issuer's whole configuration; build the dict explicitly so that a later field added to the object is not disclosed by accident, and say that in the comment. Rate limit it with `federation.enforce_rate_limit` (`shakenfist/federation.py:462-489`) keyed on the request source the way `/auth/federated` is, counted before the issuer listing, because that listing reads the database once per configured issuer and must not be reachable unmetered — read `enforce_rate_limit`'s docstring, which makes exactly this point about `issuer_claiming_url`. Return `429` on `RateLimited` the way `/auth/federated` does (`shakenfist/external_api/auth.py:1971-1978`: catch it, log with the source, and answer with `suppress_traceback=True`). Do **not** catch `DatabaseUnavailable`: `handle_database_unavailable` is in `Resource.method_decorators` (`shakenfist/external_api/base.py:2854-2862`, defined at `base.py:2473`) and already turns it into a 503 for every endpoint, which is why `/auth/federated` does not catch it either. Declare both codes in the `swag_from` response list with `requires_auth=False`. Register the route in `shakenfist/external_api/app.py` beside the issuer routes, and add `human-login-metadata` to the `auth` family of `API_CAPABILITIES` with a comment saying a client uses it to decide whether this cluster offers human login at all. Unit tests: an issuer with no `client_id` is omitted; one with a `client_id` but neither endpoint is omitted; one with `client_id` and only `device_authorization_endpoint` is included; a soft-deleted issuer is omitted; the response contains no `jwks_uri`, `audience`, `uuid` or `state` key — assert on the exact key set, not on the absence of one key; and the rate limiter is consulted before any issuer read. |
| 3e | low | haiku | none | **Confirm there is nothing to template, and stop.** Step 3b established that this step has no work: `grep -rn FEDERATION_ shakenfist/deploy/` finds only `FEDERATION_JWKS_CA_BUNDLE`, in `collection/roles/node/templates/config:78` and `collection/roles/node/meta/argument_specs.yml:381`. The other federation settings -- the two JWKS timeouts, the cache lifetime, the token size cap and the rate limit -- are not templated at all and run on their `config.py` defaults, so the two new discovery settings are consistent with every one of their siblings by being left alone. Re-run that grep to confirm it still holds, then make no change and report. The original brief follows, for the record. **Add the two new settings to the deployment templates.** Commit subject: `Template the OIDC discovery settings.` `FEDERATION_DISCOVERY_MAX_BYTES` and `FEDERATION_DISCOVERY_FETCH_TIMEOUT_SECONDS` are added to `shakenfist/config.py` by step 3b. Find how the existing `FEDERATION_JWKS_*` settings reach a deployed node — grep the ansible templates under `shakenfist/deploy/` for `FEDERATION_JWKS_FETCH_TIMEOUT_SECONDS` — and add the two new settings the same way, in the same place, in the same order they appear in `config.py`. If that grep finds that the federation settings are not templated individually, stop and report that instead of inventing a mechanism: the setting defaults are usable as they stand and a wrong template is worse than none. Change nothing else. |
| 3f | medium | sonnet | none | **Document discovery for operators and developers.** Commit subject: `Document OIDC discovery and login metadata.` Four files. In `docs/operator_guide/authentication.md`: the "Trusting an issuer is an administrative act" section (line 150) says "Four fields, all mandatory" and tables them — rewrite it for the real shape, which is two mandatory fields (`name`, `issuer_url`), `audience` still mandatory, `jwks_uri` now optional, and `client_id` and `oidc_scopes` optional and needed only for human login. Add a subsection on discovery covering: that it runs at configuration time and not on a timer (D3), which fields it fills, that the document's `issuer` must match `issuer_url` exactly and that a trailing-slash difference is the common cause of a refusal (D2, D1a), that an operator-supplied `jwks_uri` always wins and why a disagreement is only audited (D4), that redirects are not followed — cross-reference the existing "The JWKS URI must be the final URL" section at line 366 rather than restating it — and how to re-run discovery on an existing issuer. The same file has a `### Settings` table at lines 320-329 listing every `FEDERATION_*` setting with its default; add `FEDERATION_DISCOVERY_FETCH_TIMEOUT_SECONDS` and `FEDERATION_DISCOVERY_MAX_BYTES` to it, in the same style, each saying what it bounds. Step 3b found this table missing from this brief; it is the one page an operator looks at for a setting's default, so a setting absent from it is effectively undocumented. In `docs/developer_guide/authentication.md`, add discovery to the federated-identity description and say that it populates configuration and that validation still reads the issuer object, never the token. In `docs/developer_guide/api_reference/authentication.md`, document the changed `POST`/`PUT /auth/issuers` bodies, the new `POST /auth/issuers/<name>/discover`, and `GET /auth/login` including that it is unauthenticated and rate limited. In `docs/glossary.md`, extend the trusted-issuer entry for the new fields and add an entry for login-capable if the glossary's style supports one. Do not add anything to `AGENTS.md` or `ARCHITECTURE.md`: no convention and no component boundary changed — `oidc_discovery.py` is a module beside `federation.py` within an existing component. Do not add a `README.md` bullet. Check every statement against the code as it stands after 3a to 3d; this step is the one most likely to document an intention rather than the implementation. |
| 3g | high | opus | none | **Prove the security-critical assertions actually fail when broken.** Commit subject: `Mutation check the discovery guards.` Use the `mutation-check` skill. Four mutations, applied one at a time with the tree restored between each, and run the unit suite with `python -B` so a same-size mutant cannot leave live stale bytecode: (1) in `shakenfist/oidc_discovery.py`, change the `issuer` equality check to a `startswith` or a `.rstrip('/')`-normalised comparison — at least one test must fail, and it must be a test about the `issuer` mismatch, not an unrelated one; (2) remove the https check on the fetched `issuer_url`; (3) remove the redirect refusal; (4) in `shakenfist/external_api/auth.py`, make `AuthLoginEndpoint` return `external_view()` instead of the explicit dict — the exact-key-set assertion from 3d must fail. For each, record in the Results section of this plan file which test failed and its name. If any mutation survives, the test is not proving what it claims and the fix is a better test, not a note. Restore every file with the inverse edit or from a saved copy, **not** with `git checkout`, which would also discard uncommitted work elsewhere in the tree. Commit only this plan file's Results section; the mutations are never committed. |
| 3h | low | haiku | none | **File the deferred work.** Commit subject: `Record phase 3's deferred work.` No code. File one GitHub issue against `shakenfist/shakenfist`: that `_validate_issuer_arguments` (`shakenfist/external_api/auth.py:895-920`) requires https on `jwks_uri`, with a comment explaining that a plaintext JWKS fetch "turns signature verification into theatre", but applies no such check to `issuer_url` — which discovery now fetches. Say in the issue that the discovery path enforces https on what it requests, so this is about the stored field rather than about a live hole, and that the fix is a breaking change to an admin API which needs a decision about existing deployments. Do not add the `automated-fix-attempted` label.

File a second issue: `Field(max_length=...)` on a Pydantic schema field never reaches the generated DDL, because `PYTHON_TO_SQLALCHEMY` maps `str` to `sa.String(255)` unconditionally (`shakenfist/schema/sqlalchemy.py:285`) and `max_length` appears nowhere in that module. `TrustedIssuerAttributesData.issuer_url`, `jwks_uri` and `audience` have each declared `max_length=1024` since they were written and are stored as `VARCHAR(255)`; a value between 256 and 1024 characters passes validation and then errors or truncates in MariaDB. Give the reproduction: compile the column type off the table definition and compare it with the model's declared limit. Say that `SQLLongText()` (`shakenfist/schema/sqlalchemy.py:189`) is the existing escape hatch and that step 3a used it for the three discovered endpoint URLs, so this issue is about the three pre-existing columns, whose fix needs a data migration. Do not label this one either. Then add two entries to the master plan's Future work section in `docs/plans/PLAN-oidc-authentication.md`: that issue, and a periodic discovery refresher (D3 declined one; say that an endpoint which moves at the provider stays stale until an administrator re-runs discovery, and that a refresher would be a cluster-daemon job rather than a request-path fetch). Record the issue number in this plan's Results section. |

## Risks and mitigations

**The `issuer` equality check is written loosely.** This is the
one that matters. A `startswith`, a `rstrip('/')`, a `.lower()`,
or an `in` would each turn discovery from a security boundary into
a suggestion, and every one of them is a plausible thing for an
implementer to write while making an Authentik trailing slash
work. D1a exists to remove the motive — the slash is handled in
URL construction, where it belongs, and never in the comparison.
*Checked by:* step 3g mutates the comparison and requires a named
test to fail; the management session reads the comparison itself
rather than the summary, and rejects the step if the fix for the
trailing slash has migrated into D2's check.

**An existing deployment's issuer stops working.** Every issuer
configured today supplies `jwks_uri`, and 3a makes that column
nullable and 3c makes the field optional. A migration that drops
the value, or an endpoint that stops honouring it in favour of a
discovered one, would break workload exchange cluster-wide for a
feature those deployments do not use. *Checked by:* 3a's
idempotence test asserts existing rows are unchanged, and 3c's
last test is specifically an unchanged workload-style create
against a provider with no discovery document.

**The CA-bundle union gets written the short way.** `verify=` is
the obvious way to point `requests` at a bundle and it is wrong in
a way that only shows up for a deployment with *both* a private
provider and a public one — which is exactly the deployment the
existing `jwks_ssl_context()` docstring says it was written to
protect. *Checked by:* 3b's brief names the adapter approach and
forbids `verify=`; the management session greps the diff for
`verify=` before committing.

**The login endpoint grows a field.** `external_view()` is the
easy thing to return and it serves `audience` and `jwks_uri`. A
later phase adding a field to the issuer object would then
disclose it from an unauthenticated endpoint without anyone
deciding to. *Checked by:* 3d builds the dict explicitly and
asserts the exact key set; 3g mutates it to `external_view()` and
requires that assertion to fail.

**The documentation describes the plan rather than the code.**
3f is written against four files whose current text this plan
quotes, and the "four fields, all mandatory" sentence is about to
become false in a way a reader will act on. *Checked by:* 3f runs
after 3a-3d and its brief says to verify each statement against
the tree; the management session spot-checks the mandatory-field
table against `_validate_issuer_arguments` as it stands.

**Scope creep into phase 7.** Having built the metadata endpoint,
the next obvious thing is a flow to consume it, and that cannot
land in this repository. *Checked by:* the Scope section names it;
the Definition of done asserts no file outside this repository's
server and docs trees changed, and that nothing in the diff
implements a token request.

## Definition of done

Each item is falsifiable, and the command is given where there is
one. Run from the phase worktree unless stated.

* [ ] Phase 0's row in the master plan's Execution table reads
      `Complete` with `2bbe472eff` in `Merged`, and phase 3's
      reads `In progress` with its `Plan` cell linking this file.
      `docs/plans/index.md`'s row for the plan reads `1 of 11`.
      `python3 tools/check-plan-status.py` passes.
* [ ] `grep -c 'Four fields, all mandatory'
      docs/operator_guide/authentication.md` returns 0. That
      sentence is false after 3c and a reader will act on it.
* [ ] No fact about discovery is stated differently on two pages.
      Specifically: the operator guide, the developer guide and
      the API reference agree on whether `jwks_uri` is mandatory,
      and none of them says discovery runs periodically.
* [ ] `grep -rn 'verify=' shakenfist/oidc_discovery.py` returns
      nothing, and the module's session mounts an adapter built
      from `federation.jwks_ssl_context()`.
* [ ] The `issuer` comparison is `==` against the value passed
      in to `fetch()`. Not `startswith`, not `in`, not `.lower()`,
      and no `rstrip` on either side of it:
      `grep -n 'rstrip\|startswith\|lower()' shakenfist/oidc_discovery.py`
      returns nothing outside `discovery_url()`. As built it
      returns nothing at all, because the slash is stripped with
      `endswith` and the scheme checks parse the URL rather than
      matching a prefix; no output is a pass, not a sign the
      implementation is missing.
* [ ] A trailing-slash issuer URL produces exactly one slash in
      the fetch URL. There is a unit test named for this case and
      it asserts the full URL string, not a substring.
* [ ] `GET /auth/login` returns an exact key set per issuer, and a
      unit test asserts that set rather than the absence of
      individual keys. `jwks_uri`, `audience`, `uuid` and `state`
      appear in neither the implementation of the response dict
      nor the test's expected set.
* [ ] An issuer created with `jwks_uri` supplied and no reachable
      discovery document is created successfully, with an audit
      event on it. There is a unit test for this and it is the
      one that proves no existing deployment regresses.
* [ ] A version-1 `trusted_issuer_attributes` table migrates to
      version 2, gains all six columns, has a nullable
      `jwks_uri`, leaves existing row values unchanged, and the
      migration is idempotent across two runs. Four unit tests.
* [ ] `docs/operator_guide/database.md` lists all six new columns
      for `trusted_issuer_attributes`:
      `grep -A2 'trusted_issuer_attributes' docs/operator_guide/database.md`
      shows them.
* [ ] `issuer-discovery` and `human-login-metadata` are both in
      the `auth` family of `API_CAPABILITIES`, each with a
      comment: `grep -n 'issuer-discovery\|human-login-metadata'
      shakenfist/external_api/app.py`.
* [ ] All four mutations in step 3g killed, each by a named test
      recorded in this file's Results section. A surviving
      mutation is a failed Definition of done, not a note.
* [ ] Nothing in the diff performs an OIDC token request, device
      authorization request, or PKCE exchange — that is phase 7,
      in another repository:
      `git diff develop... | grep -i 'grant_type\|code_verifier\|device_code'`
      returns nothing outside `docs/plans/`.
* [ ] `git diff --stat develop... -- ':!docs/plans'` touches only
      `shakenfist/schema/trusted_issuer_attributes.py`,
      `shakenfist/mariadb.py`, `shakenfist/config.py`,
      `shakenfist/exceptions.py`, `shakenfist/oidc_discovery.py`,
      `shakenfist/trusted_issuer.py`,
      `shakenfist/external_api/auth.py`,
      `shakenfist/external_api/app.py`, `protos/database.proto`,
      files under `shakenfist/protos/`, files under
      `shakenfist/tests/`, files under `docs/`, and whatever 3e
      finds under `shakenfist/deploy/`.
* [ ] `docs/plans/order.yml` is unmodified:
      `git diff --exit-code develop... -- docs/plans/order.yml`.
* [ ] The deferred issue from 3h exists, its number is recorded in
      Results, and both Future work entries are in the master
      plan.
* [ ] `pre-commit run --all-files` passes.

## Back brief

Before executing any step, back brief the operator on the
understanding of this plan and how the intended work aligns with
it.

**There is one gate, and it is before step 3d.** Decision D7
widens this phase beyond what the master plan's phase 3 section
describes: it adds a public, unauthenticated endpoint that
discloses which identity provider a cluster trusts for human
login. The argument for putting it here is in D7 and it is an
argument about repository boundaries rather than about design, so
it is exactly the kind of thing a reviewer may reasonably decide
differently. An unauthenticated endpoint is also cheap to propose
and awkward to withdraw once a client depends on it.

So when 3a, 3b and 3c are complete, the management session states
the field list it intends 3d to serve and waits for Michael before
releasing 3d. Steps 3a, 3b, 3c, 3e, 3f, 3g and 3h need no gate.

A second, lighter check: step 3a changes a schema. If its
migration branch needs anything more than guarded `ADD COLUMN`
statements and one `MODIFY COLUMN`, say so before committing it
rather than after.

## Execution notes

Departures from the plan, and facts the plan got wrong, recorded
as they happen.

* **2026-11-11 (AEDT), before step 3a.** Checking step 3a's brief
  against the tree found one wrong premise and corrected it at
  source. The brief specified `discovered_at` as `Optional[int]`
  "mirroring how other attribute tables store times"; the closest
  sibling is `NamespaceKeyAttributesData.expiry`
  (`shakenfist/schema/namespace_key_attributes.py:75`), a nullable
  time on an attributes table, and it is `Optional[float]`. The
  same check found that the same file models
  `scopes: Optional[list[str]]`, so a JSON list column is
  available in this codebase and `oidc_scopes` being a string is a
  choice rather than a constraint. The choice stands — the value
  is passed through verbatim to the provider — but the brief now
  says so explicitly rather than leaving an agent to discover the
  sibling and "fix" it.

* **2026-11-11 (AEDT), back brief.** Michael kept the gate before
  step 3d, authorised step 3h to file its issue, and gave this
  session permission to push the branch and open the pull request
  once the Definition of done passes.

* **2026-11-11 (AEDT), step 3a: the gRPC proto was missing from
  the brief.** Step 3a's brief described the Pydantic model and the
  table migration and said nothing about the wire format.
  `TrustedIssuerAttributesProto` (`protos/database.proto:2111`) had
  four fields, so every new column would have been silently dropped
  on any write that goes through the database microservice -- which
  is the path a real cluster uses, the direct path being used only
  by the daemon itself. The step was extended to add the six fields
  to the proto and regenerate the stubs. The database daemon needed
  no change of its own: its `CreateTrustedIssuerAttributes`,
  `GetTrustedIssuerAttributes` and `UpdateTrustedIssuerAttributes`
  handlers (`shakenfist/daemons/database/main.py:4350-4405`) call
  only the two `mariadb._trusted_issuer_attrs_*_proto` helpers.

  Steps 3b, 3c and 3d add no proto fields, so no later step of this
  phase needs to touch `protos/` again.

* **2026-11-11 (AEDT), step 3a: `Field(max_length=...)` does not
  reach the DDL, which changed what the URL columns are.**
  `PYTHON_TO_SQLALCHEMY` maps `str` to `sa.String(255)`
  unconditionally (`shakenfist/schema/sqlalchemy.py:285`) and
  `max_length` appears nowhere in that module, so the plan's
  `max_length=1024` would have been a Pydantic-only limit over a
  `VARCHAR(255)` column. The three discovered endpoint URLs now
  carry the `SQLLongText()` marker
  (`shakenfist/schema/sqlalchemy.py:189`) and really are LONGTEXT;
  `client_id` and `oidc_scopes` declare 255 instead of 1024 so the
  model's claim matches its column. Verified by compiling each
  column's type off the table definition rather than from the
  model.

  The same defect affects `issuer_url`, `jwks_uri` and `audience`,
  which have claimed 1024 and stored 255 since they were written.
  That predates this plan and needs a data migration, so step 3h
  files it rather than this phase fixing it.

* **2026-11-11 (AEDT), step 3a: nullable strings have two
  conventions here, and this phase uses the stricter one.** The
  sub-agent first carried a null `jwks_uri` as an empty string,
  which matches `NetworkInterfaceAttributesData.floating_address`
  (`protos/database.proto:1805`, commented "empty = None", with an
  `or ''` coercion at `shakenfist/mariadb.py:18082`) -- safe there
  because an empty string is not a legal IP address. The management
  session's first reading called that simply wrong, which was too
  strong; it is one of two conventions in the tree.

  The fields here use the other one, proto3 field presence with
  `HasField`, as `NamespaceKeyAttributesData` does
  (`shakenfist/mariadb.py:14357-14398`). The reason is specific
  rather than stylistic: an empty `client_id` or `oidc_scopes` is
  plausible operator input, so the update path in step 3c has to be
  able to tell "clear this field" from "leave it alone", and
  collapsing null and empty would remove that distinction before
  3c is written. Confirmed on a real serialize-and-parse round
  trip, not only in memory: an empty string travels as 54 bytes and
  returns `''`, a null as 52 bytes and returns `None`.

* **2026-11-11 (AEDT), step 3b: two later steps amended on its
  findings.** Step 3e has no work. Only `FEDERATION_JWKS_CA_BUNDLE`
  is templated anywhere under `shakenfist/deploy/`; the other six
  federation settings run on their `config.py` defaults, so the two
  new discovery settings are consistent with their siblings by being
  left alone. 3e's brief now says so and tells its agent to confirm
  the grep and stop. Step 3f's brief missed the `### Settings` table
  at `docs/operator_guide/authentication.md:320-329`, which lists
  every `FEDERATION_*` setting with its default -- the one page an
  operator reads for a default, so a setting missing from it is
  effectively undocumented. 3f now covers it.

* **2026-11-11 (AEDT), step 3b: refusal messages bound what they
  quote.** Added by the management session rather than the
  sub-agent, after confirming the case was reachable: a document
  whose `issuer` member is 60,000 characters produced a 60,200
  character refusal, which would have gone into the administrator's
  response and into this module's log line. Values quoted back in a
  refusal are now truncated at 120 characters with their true
  length reported.

  Worth recording because the first test written for it passed for
  the wrong reason. Building the document with the suite's own
  helper repeats the issuer into every endpoint member, which
  inflates the body past `FEDERATION_DISCOVERY_MAX_BYTES` so the
  size refusal fires first -- the test exercised the body cap and
  not the truncation. The body cap does bound this indirectly, but
  only to 64KB, and a single enormous member in a document that
  still fits is easy to construct. The test now builds that
  document by hand and says why.

* **2026-11-11 (AEDT), step 3b: a deliberate departure kept.** The
  brief put the `requests` exception handler around the request;
  the sub-agent extended it to cover the body read as well, so that
  a connection reset part way through a response reaches an admin
  endpoint as a discovery failure rather than as a 500. Kept.

* **2026-11-11 (AEDT), step 3a: five unrelated stub version
  markers.** `tox -e genprotos` also rewrote
  `GRPC_GENERATED_VERSION` from 1.84.0 to 1.84.1 in five generated
  modules this phase does not otherwise touch.
  `database_pb2_grpc.py` already read 1.84.1 on `develop`, so the
  drift predates this phase and regenerating corrects it. Kept
  rather than reverted, because reverting leaves the tree
  inconsistent with its own generator and hands the diff to
  whichever unrelated change next runs `genprotos`; committed on
  its own so the discovery diff stays discovery.

## Results

Filled in as the steps complete.

### Step 3b — the discovery fetcher

Landed as `Fetch an issuer's OIDC discovery document.`

`shakenfist/oidc_discovery.py`, with `discovery_url()` and
`fetch()`. The issuer comparison is a plain `==` against the
parameter at `shakenfist/oidc_discovery.py:371`. Refusal order:
scheme, status, redirect, body cap, JSON, `issuer` present,
`issuer` equal, `jwks_uri` present, `jwks_uri` https, then each
optional endpoint https-when-present. TLS verification goes
through a mounted adapter carrying `federation.jwks_ssl_context()`;
`grep -c 'verify=' shakenfist/oidc_discovery.py` returns 0. Two
settings added, both defaulting as the plan specified: timeout 5,
cap 65536.

Twenty-four unit tests, all passing, no network. The management
session re-ran them, confirmed URL construction against Authentik's
trailing-slash issuer, Keycloak's realm path and GitHub's bare
host, and confirmed both of the module's DoD greps.

### Step 3a — the discovery columns

Landed as `Add discovery columns to trusted issuers.`, with the
unrelated stub version markers split into `Sync five stale gRPC
stub version markers.`

Six attributes added to `TrustedIssuerAttributesData`:
`authorization_endpoint`, `token_endpoint` and
`device_authorization_endpoint` as LONGTEXT; `client_id` and
`oidc_scopes` as `VARCHAR(255)`; `discovered_at` as `DOUBLE`.
`jwks_uri` is now nullable. `trusted_issuer_attributes` moved to
table version 2, migrated by guarded `ADD COLUMN` statements whose
types are compiled from the table definition, plus one `MODIFY
COLUMN` to relax `jwks_uri`; the migration emits seven statements
and the test asserts that count exactly. `TrustedIssuerAttributesProto`
gained the six fields with proto3 presence and `jwks_uri` became
`optional` at the same number and type.

Eight unit tests in
`shakenfist/tests/test_mariadb_trusted_issuer_attributes_schema.py`,
all passing. Two were not asked for and were kept:
`test_table_definition_matches_model`, which is what would catch
the next model field that never reaches a column, and
`test_empty_string_is_not_none`, which pins the presence decision
above.

The management session re-ran the suite itself and independently
confirmed three of the sub-agent's claims against the tree: that a
1025-character endpoint is still rejected while 1024 is accepted,
the compiled type of every new column, and that presence survives
a real wire round trip. Nothing read, written or verified needed a
live cluster.
