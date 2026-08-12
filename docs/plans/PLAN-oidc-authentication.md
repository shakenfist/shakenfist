# OIDC authentication for Shaken Fist

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
relevant source files, understand existing patterns (object
lifecycle, state machines, MariaDB storage via the three-layer
direct/gRPC/public pattern, Pydantic schemas, daemon
architecture, operation queue system, event logging), and
ground your answers in what the code actually does today. Do
not speculate about the codebase when you could read it
instead. Where a question touches on external concepts (OIDC,
OAuth 2.0, JWT validation, JWKS rotation, PKCE, device-code
flow, Keycloak/Authentik client modelling, group/claim
mapping), research as needed to give a confident answer. Flag
any uncertainty explicitly rather than guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview, object types, and daemon structure. Consult
`CLAUDE.md` for build commands, project conventions, and
database access patterns. Consult `GOALS.md` for current
development priorities. Key references inside the repo:

* `docs/glossary.md` — the authentication vocabulary, pinned
  by phase 1 of the auth federation plan. Use these words
  rather than inventing synonyms for them.
* `docs/plans/PLAN-auth-federation.md` — the sibling plan,
  which built the machine half of federation. Its phases 1
  to 4 are the infrastructure this plan builds on.
* `shakenfist/external_api/auth.py` — `/auth`, the namespace
  and key CRUD endpoints, the trusted-issuer and
  mapping-rule endpoints, and `POST /auth/federated`.
* `shakenfist/external_api/base.py` — `verify_token`,
  `caller_is_admin`, `_enforce_scope`, and the
  `Resource.method_decorators` list that makes
  authentication universal.
* `shakenfist/external_api/scopes.py` — the scope
  vocabulary, its derivation from resource class and HTTP
  method, and `satisfies()`.
* `shakenfist/util/access_tokens.py` — the JWT issue and
  parse helpers built on `flask_jwt_extended`.
* `shakenfist/namespace_key.py` and
  `shakenfist/schema/namespace_key_attributes.py` — keys as
  first-class objects, with expiry, scopes and provenance.
* `shakenfist/namespace.py` — the `Namespace` DBO, the trust
  model, and the inter-node `_service_key_*` path.
* `shakenfist/trusted_issuer.py`,
  `shakenfist/mapping_rule.py` and
  `shakenfist/federation.py` — external issuer trust, claim
  matching, and external token validation.
* `docs/{developer,operator,user}_guide/authentication.md`
  — the current authentication documentation surface.

`sf-client` is **not** in this repository. It lives in
`client-python`; this repo ships only `sf-ctl` and
`sf-backup` (`pyproject.toml:157-158`). Anything to do with
client-side login flows or credential caching happens
there, not here.

When we get to detailed planning, the convention is a
separate plan file per detailed phase, named
`PLAN-oidc-authentication-phase-NN-descriptive.md` in the
same directory.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Phases 1 to 4 of `PLAN-auth-federation.md` rebuilt the
machinery this plan was originally written against. Every
numbered item below exists in the tree today; this plan
builds on it rather than proposing it. The vocabulary is
the one pinned in `docs/glossary.md`.

1. **Namespace keys are first-class objects.** A
   [namespace key](/glossary/#namespace-key) is a
   `NamespaceKey` DBO (`shakenfist/namespace_key.py:62`)
   living in the `namespace_keys` and
   `namespace_key_attributes` tables, carrying a bcrypt
   hash, a [nonce](/glossary/#nonce), an optional expiry,
   an optional [scope](/glossary/#scope) list and an
   optional provenance dict
   (`shakenfist/schema/namespace_key_attributes.py:24-70`).
   The `keys` JSON column on `namespace_attributes` that
   used to hold them is vestigial — "neither read nor
   written any more"
   (`shakenfist/namespace.py:187-190`). Beware that
   `Namespace.keys` still *synthesises* the legacy
   `{'nonced_keys': ...}` shape out of the tables
   (`shakenfist/namespace.py:197-205`) for the handful of
   call sites that still read it, so grepping `nonced_keys`
   finds a compatibility view rather than a live column.
2. **`/auth` mints access tokens from keys.** The endpoint
   in `shakenfist/external_api/auth.py` takes a
   `{namespace, key}` body, bcrypt-compares the presented
   secret against the namespace's unexpired keys, and
   issues an [access token](/glossary/#access-token) via
   `flask_jwt_extended.create_access_token`. Cluster-minted
   secrets carry an `sfk_` prefix and a base62 CRC32
   checksum so a leak is greppable and a scanner can reject
   lookalikes offline (`shakenfist/util/credentials.py`).
3. **JWT identity is `<namespace>:<keyname>`** — see
   `shakenfist/util/access_tokens.py`. Alongside it the
   token carries `iss`, the minting key's `nonce`, and a
   `scopes` list
   (`shakenfist/util/access_tokens.py:42-47`). The nonce is
   re-verified against the stored key on every request, so
   rotating or deleting a key immediately invalidates every
   outstanding token minted from it. The default token
   lifetime is fifteen minutes
   (`config.API_TOKEN_DURATION`).
4. **Authentication and scope enforcement are universal.**
   Both run from `Resource.method_decorators`
   (`shakenfist/external_api/base.py:1288-1296`), so a new
   endpoint is authenticated and scope-checked without
   anyone remembering to decorate it. `@api_base.public` is
   the only way out, and the public set is written down and
   individually justified. The scope check itself is
   `_enforce_scope` (`shakenfist/external_api/base.py:1234`),
   comparing the token's `scopes` claim against a scope
   derived from the resource class and the HTTP method.
5. **`caller_is_admin` is two axes, not one.** An
   administrative endpoint now requires **both** the
   `system` namespace and the `cluster-admin` scope
   (`shakenfist/external_api/base.py:128-148`), so a key
   scoped to, say, `blob.read` but minted into `system`
   cannot escalate. Legacy unscoped keys carry the wildcard
   and are unaffected.
6. **Trusted issuers are cluster-level objects.** A
   [trusted issuer](/glossary/#trusted-issuer) is a
   `TrustedIssuer` DBO
   (`shakenfist/trusted_issuer.py:41`) with exactly three
   attributes — `issuer_url`, `jwks_uri` and `audience` —
   managed by an administrator under `/auth/issuers`. The
   JWKS location is operator-supplied and required; it is
   deliberately never taken from the token.
7. **Mapping rules are namespace-owned objects.** A
   [mapping rule](/glossary/#mapping-rule) is a
   `MappingRule` DBO (`shakenfist/mapping_rule.py:244`)
   managed under `/auth/namespaces/<namespace>/rules` and
   gated by `requires_namespace_ownership`. It names an
   issuer, the claims an inbound token must satisfy, the
   scopes to grant, a key TTL and a key name prefix. Claim
   matching is **exact only**: an exact string, or
   membership of a list of exact strings, with no globbing,
   regular expressions, prefix matching or coercion
   (`shakenfist/federation.py:347-363`).
8. **An external identity can already be exchanged for a
   credential.** `POST /auth/federated`
   (`shakenfist/external_api/app.py:383`, handler at
   `shakenfist/external_api/auth.py:1258`) is
   unauthenticated by `@api_base.public` — its
   authentication *is* the presented token — takes
   `{token, namespace, rule}`, and returns
   `{namespace, key_name, key}`. The
   [identity token](/glossary/#identity-token) is
   validated, the rule's claims matched, and a scoped,
   time-bounded namespace key minted with provenance
   recording the rule, the issuer and the claims that were
   actually satisfied.
9. **External token validation is written and hardened.**
   `shakenfist/federation.py` verifies signatures against a
   pinned RS/ES/PS algorithm allowlist with HS deliberately
   absent (`shakenfist/federation.py:39-51`), requires
   `exp`, `iss` and `aud`, and allows zero clock skew
   (`shakenfist/federation.py:321-340`). JWKS fetching goes
   through `PyJWKClient` with per-issuer caching and a
   per-issuer lock, so a key rotation collapses concurrent
   misses into one fetch rather than a stampede
   (`shakenfist/federation.py:120-210`).
10. **Trust between namespaces** is unchanged: a list on
    `namespace_attributes.trust` granting visibility from
    trusted namespaces into the trusting namespace, with
    `system` in every namespace's list and unremovable. See
    [trust](/glossary/#trust), which is a different concept
    from a trusted issuer and must not be conflated with
    it.
11. **Inter-node authentication** still reuses the key
    path, via short-lived `_service_key_<rand>` keys minted
    per request (`shakenfist/namespace.py:386-410`). Those
    keys now live on the new tables, use `sfk_`-format
    secrets, expire after five minutes and mint five-minute
    tokens.

What this model gets right:

* Bearer credentials remain excellent for automation — CI
  systems, Ansible and the SF Python client hold a key and
  call `/auth` when they need a token — and they are no
  longer necessarily long-lived or all-powerful, because a
  key can carry an expiry and a scope list.
* Namespace ownership is unambiguous: the key *is* a
  capability on that namespace.
* JWT format and `Authorization: Bearer ...` semantics are
  already in place, so the wire shape will not change much.
* Revocation is immediate and cheap. The nonce check on
  every request means deleting a key kills its tokens now,
  rather than after a token lifetime.
* Enforcement is already in exactly one place. Anything
  this plan adds inherits universal authentication and
  scope checking rather than having to re-plumb it.
* Half of an OIDC relying party is built and issuer-generic
  by construction. Trusted issuers and mapping rules are
  data, so adding an Authentik or Keycloak issuer beside
  the GitHub Actions one is configuration, not code.

What it does not give us:

* **No human SSO story.** A human operator still cannot
  "log in with their corporate identity". The federated
  exchange assumes the caller already holds a signed
  identity token, which is true of a CI job and false of a
  person at a terminal. A human is still issued a static
  key and puts it in a file.
* **No central account lifecycle.** Disabling a person
  means finding and deleting keys across every namespace
  they had access to; there is still no notion of "this
  human" independent of "this namespace key".
* **No group-driven namespace access.** A mapping rule is
  owned by, and grants into, exactly one namespace, and its
  claim matching is exact. There is no "engineering group
  has access to these N namespaces" primitive: a group of
  twenty namespaces is twenty rules, each written by
  someone who owns that namespace.
* **No OIDC client.** Phase 3 built the *verification* half
  of a relying party and none of the *client* half. Nothing
  in the tree fetches `.well-known/openid-configuration` —
  `jwks_uri` is operator-supplied and required — so there
  is no discovery, and there is nothing that *initiates* a
  flow. A workload arrives holding a minted token; a human
  has to start a conversation with the IdP, and the
  endpoints that conversation needs are exactly what a
  discovery document publishes.
* **One namespace per credential.** Every credential Shaken
  Fist mints names exactly one namespace, and
  `request_namespace()` is a string split returning one
  name (`shakenfist/util/access_tokens.py:76`). A human is
  typically in several namespaces. The open questions below
  take this up; it is recorded here because it is a fact
  about the code, not because it is settled.
* **Nothing on the client side.** `sf-client` lives in the
  separate `client-python` repository and has no login or
  OIDC code; this repo ships only `sf-ctl` and `sf-backup`
  (`pyproject.toml:157-158`). None of phase 3's new route
  families are wrapped by the client either.

### Relationship to the auth federation plan

`PLAN-auth-federation.md` is the *machine* half of
federation and has been largely built: a workload exchanges
an IdP-issued identity token for a scoped namespace key.
This plan is the *human* half. The two share everything up
to and including token validation — trusted issuers, JWKS
fetch and rotation, signature and claim verification — and
this plan should add no second copy of any of it.

Where they may diverge is what happens after validation.
The federation plan mints a key and lets every existing
consumer carry on unchanged. This plan's original design
authorised requests directly off the external token. Whether
human login should follow the exchange pattern instead is
the central unresolved question below, and the auth
federation plan explicitly reserves it for this plan's own
phase 0 decisions pass rather than answering it in passing.

Two things the federation plan settled are worth restating
because they constrain this one. Outsourcing token issuance
does not outsource authorisation: Shaken Fist still has to
map an external identity onto namespaces and scopes, and
that policy is irreducibly its own. And namespace keys are
not going away — machine credentials are a genuine need,
which is why GitHub and GitLab keep personal access tokens
alongside SSO.

## Mission and problem statement

Give Shaken Fist a human login story: let a person
authenticate to the REST API with their corporate identity,
have their namespace access follow from their group
membership in the identity provider, and keep every existing
credential path working while that happens.

Concretely:

* **Humans** can authenticate to the SF REST API using
  their corporate identity, via an OIDC flow appropriate to
  the client — device code for a CLI on a headless box,
  authorisation code with PKCE where a browser is
  available. This needs the client half of a relying party
  that does not exist yet: discovery to learn the issuer's
  endpoints, and flow initiation to use them.
* **Namespace access for humans** is driven by claims in
  the IdP-issued token, typically derived from group
  membership. A person gains and loses access by being
  added to or removed from groups in the IdP, with no
  SF-side per-user bookkeeping. Since a person is usually
  in several namespaces, and every credential today names
  exactly one, this is a change to the shape of
  authorisation rather than a new claim reader.
* **Machines** keep the credential model they have. Keys
  are already first-class objects with expiry, scopes and
  provenance; what remains here is whether they are
  re-presented to users as service-account credentials, and
  under what names. Operators who prefer to outsource
  machine credentials to their IdP already can, through the
  federated exchange.
* **Authorisation lives in one place.** It already does —
  universal authentication and scope enforcement on
  `Resource.method_decorators` — and nothing this plan adds
  may create a second path. In particular, no phase may
  quietly weaken `satisfies()`'s treatment of a missing
  `scopes` claim as a wildcard, which is safe for tokens
  Shaken Fist minted and is not obviously safe for tokens
  it did not.
* **Inter-node authentication never depends on the IdP.**
  Cluster nodes already have a trust relationship that
  gains nothing from federating through an external
  provider, and making the IdP a hard dependency of cluster
  operation is a reliability regression, not a security
  improvement.
* **Existing deployments keep working.** OIDC is opt-in and
  configured per cluster. A cluster that never enables it
  behaves exactly as it does today.

Scope boundaries:

* **In scope:** OIDC discovery, which was not built;
  interactive flow initiation and whatever client-side
  login command drives it; claim-driven namespace
  authorisation for humans, including the multi-namespace
  question; whether `caller_is_admin` can drop its
  namespace half now that a `cluster-admin` scope exists;
  the naming and framing of namespace keys as
  service-account credentials; worked operator
  documentation for configuring Keycloak and Authentik;
  and functional coverage against a containerised IdP.
* **Out of scope:** rebuilding JWT signature validation,
  JWKS fetch, caching or rotation. `shakenfist/federation.py`
  does this and any new work extends it.
* **Out of scope:** the workload exchange itself, which is
  `PLAN-auth-federation.md`'s subject and is built.
* **Out of scope:** running an IdP inside SF. SF is the
  *relying party*; operators bring their own IdP.
* **Out of scope:** SAML, LDAP-direct, or other non-OIDC
  identity protocols. OIDC is the lingua franca and is the
  one we will support.
* **Out of scope:** changing inter-node authentication.
* **Out of scope (initially):** extending the scope
  vocabulary beyond what phase 3 of the auth federation
  plan shipped. Scopes already give a verb-level axis; the
  unit of *identity* remains the namespace, and
  finer-grained roles are future work.
* **Out of scope (initially):** UI / web console for
  login. SF does not ship a web UI; the OIDC flows are
  driven by the CLI client.

## Open questions

1. **Issuer trust model.** How many IdPs can a cluster
   trust at once? One feels limiting (you might want
   "internal IdP for staff, partner IdP for contractors").
   Many means SF carries a list of trusted issuers and
   JWKS URLs in config. Possible resolution: support a
   list, validate the token's `iss` against the list, and
   pick the matching JWKS for signature verification.

   **Resolved by auth federation phase 3 (2026-08-12).**
   Many, and as objects rather than as configuration. A
   [trusted issuer](/glossary/#trusted-issuer) is a
   `TrustedIssuer` DBO (`shakenfist/trusted_issuer.py:41`)
   carrying exactly `issuer_url`, `jwks_uri` and `audience`
   (`shakenfist/schema/trusted_issuer_attributes.py:45-53`),
   created and deleted by an administrator under
   `/auth/issuers` (`shakenfist/external_api/app.py:376-378`).
   A cluster trusts as many issuers as it has rows, so
   "internal IdP for staff, partner IdP for contractors" is
   two API calls rather than a config edit and a restart.
   The unverified `iss` is checked against the allowlist
   before any network fetch, and the JWKS comes from the
   issuer's configured `jwks_uri` and never from the token.

   The question guessed at a config list, and the difference
   is worth naming: objects have events, states, an API and
   a lifecycle, so "who may vouch for identities here" is
   auditable and changeable at runtime rather than being a
   file an operator has to remember to keep in sync across
   nodes.

   One consequence the federation plan records, and which
   this plan inherits rather than gets to re-decide: mapping
   rules reference their issuer **by name**, not by uuid, so
   deleting an issuer and recreating it under the same name
   silently rebinds every rule that named it. Storing the
   uuid would fail loudly instead. The name was kept because
   it is what an operator writes and reads, and the
   behaviour is called out in the operator guide. Any
   human-facing issuer management this plan adds has exactly
   the same property.

2. **Claim → namespace mapping.** The simplest design is a
   single claim (configurable name, e.g. `sf_namespaces`)
   that carries a list of namespace names. Alternatively,
   group names in the IdP can be mapped to namespaces via
   SF-side config (e.g. group `eng-platform` → namespaces
   `platform`, `platform-ci`). The first is cleaner but
   pushes the mapping problem entirely onto IdP admins;
   the second keeps the policy in SF but adds config
   surface. Need to pick one (or support both).

   **Half answered by auth federation phase 3; the human
   half is still open (2026-08-12).** For workloads this is
   settled, and neither of the two options won outright. A
   [mapping rule](/glossary/#mapping-rule) is a
   `MappingRule` DBO (`shakenfist/mapping_rule.py:244`)
   owned by one namespace, managed under
   `/auth/namespaces/<namespace>/rules`, which binds a set
   of exact claims and grants into exactly that one
   namespace. Claim matching is exact only — an exact
   string, or membership of a list of exact strings, with no
   globbing, regular expressions, prefix matching or
   coercion (`shakenfist/federation.py:347-363`). So the
   policy lives in Shaken Fist, as the second option wanted,
   but it is expressed as one object per grant, owned by the
   namespace being granted into, rather than as a
   cluster-level group-to-namespace table an administrator
   maintains.

   The human half is what remains, and it is harder than
   this question realised. A person is typically in several
   namespaces, so the first option — a single claim carrying
   a list of namespace names — cannot be served by minting
   one credential per exchange, because every credential
   Shaken Fist mints names exactly one namespace. That
   collision is now open question 14, and this question
   cannot close ahead of it: for humans, "which namespaces
   does this claim grant" and "can one credential name more
   than one namespace" are the same question asked twice.

3. **Token shape interop.** Today SF's tokens carry
   `sub: '<namespace>:<keyname>'` and a `nonce` claim.
   OIDC tokens carry standard claims (`sub`, `iss`,
   `aud`, `exp`, group claims) and no SF nonce. The
   request-handling code needs to discriminate between
   "SF-issued legacy token" and "IdP-issued OIDC token"
   and validate each correctly. The decorator stack in
   `external_api/auth.py` and the helpers in
   `util/access_tokens.py` need a refactor; the
   request-side `request_namespace()` becomes a
   per-request authorisation decision rather than a
   string split.

   **Partly resolved by auth federation phase 3
   (2026-08-12).** The refactor this question asks for
   happened, but for a different reason and only on one
   side. Authentication is now universal: it runs from
   `Resource.method_decorators`
   (`shakenfist/external_api/base.py:1291-1298`) rather than
   from 120 hand-applied decorators, with `@api_base.public`
   the only exemption. That was done as step 3a of the auth
   federation plan so that scope enforcement could be added
   to an already-universal path, not to make room for a
   second token shape — but it has the effect this question
   wanted, which is that there is now exactly one place
   where a request's credential is examined, and
   discriminating between shapes is a change to one function
   rather than to a decorator stack.

   The description of the token is also stale. A Shaken Fist
   access token carries `iss`, the minting key's `nonce` and
   a `scopes` list alongside `sub`
   (`shakenfist/util/access_tokens.py:42-47`). `scopes` is
   the interesting one, because it is precisely the claim an
   IdP-issued token will not have — see open question 13.

   What has **not** happened is the second half.
   `request_namespace()` is still a string split over a
   two-component identity
   (`shakenfist/util/access_tokens.py:68-76`), called from
   fifty-seven sites outside the tests. Turning it into "a
   per-request authorisation decision" is entirely
   untouched, and is large enough that it is now open
   question 14 in its own right rather than a clause here.

4. **Audience and multi-tenant clusters.** OIDC tokens
   are issued to an `aud` (audience). SF should validate
   that the token's audience matches the cluster's
   configured audience identifier so that a token minted
   for some other service is not accepted as an SF
   token. What is the right default audience name?
   Configurable per cluster.

   **Resolved by auth federation phase 3 (2026-08-12).** The
   validation is built, and the answer to "what is the right
   default audience name" turned out to be that there is no
   default and there should not be one. Each trusted issuer
   carries its own mandatory `audience`
   (`shakenfist/schema/trusted_issuer_attributes.py:52-53`),
   and validation requires `exp`, `iss` and `aud` to be
   present, verifies all three, and allows zero clock leeway
   (`shakenfist/federation.py:321-340`, with
   `LEEWAY_SECONDS = 0` at `shakenfist/federation.py:68`).

   Per-issuer rather than per-cluster because the audience
   an IdP stamps is the IdP's decision, not ours. It is a
   property of how that provider and its clients are
   configured, and a cluster trusting two providers cannot
   name one string that both will emit. A cluster-wide
   default would have been overridden by the first operator
   who added a second issuer, which is the case this
   question was worried about in the first place.

5. **What about the `system` namespace?** Today `system`
   is the bootstrap superuser and is in every namespace's
   trust list. Under OIDC, "is this caller a cluster
   admin" should be driven by a claim (e.g. a group
   `sf-admin`), not by membership in a namespace named
   `system`. The `system` namespace stays as the
   bootstrap / system-key holder; the admin *role* is
   what becomes a claim. Need to decide how the existing
   `caller_is_admin` decorator changes.

   **Partly resolved by auth federation phase 3
   (2026-08-12).** Half of the change is built.
   `caller_is_admin` now requires **both** the `system`
   namespace and a `cluster-admin` scope
   (`shakenfist/external_api/base.py:128-148`), so the
   administrative role already exists as something a token
   carries rather than as something a namespace name
   implies. Legacy unscoped keys hold the wildcard and are
   unaffected, so existing administrative automation was not
   disturbed.

   One design detail constrains anything further, and is
   recorded here so it is not undone by accident:
   `cluster-admin` is hyphenated rather than dotted
   precisely so that it names no family and therefore no
   family wildcard can synthesise it
   (`shakenfist/external_api/scopes.py:31-47`). Of the
   twenty methods `caller_is_admin` guards, only two derive
   an `admin.*` scope; the rest derive `node.*`, `issuer.*`,
   `auth.*` and `blob.read`. A dotted `admin.*` would have
   been both too narrow for what the marker gates and too
   broad for what it grants.

   What remains is exactly this question's own framing:
   whether the namespace half can now be dropped, so that
   holding `cluster-admin` is sufficient and `system`
   becomes only the bootstrap key holder. The mechanism is
   in place, so that is a decision rather than an
   implementation — and it should be taken alongside open
   question 13, because an administrator arriving from an
   IdP with no `scopes` claim at all is the case that makes
   it matter.

6. **Service account tokens vs IdP service accounts.**
   Operators may want to outsource even machine tokens
   to their IdP (Keycloak service accounts +
   `client_credentials`, Authentik service-account tokens).
   That is fine and SF will accept them like any other
   OIDC token. But SF should continue to issue its own
   service-account tokens too, for the small-cluster
   operator who doesn't want to run an IdP at all. The
   current namespace-key code becomes that path,
   renamed.

   **Partly resolved by auth federation phase 3, and
   reframed (2026-08-12).** The question assumes two options
   and phase 3 supplied a third it did not consider. It
   assumes Shaken Fist would have to *accept* an IdP-issued
   machine token directly as a bearer credential — "SF will
   accept them like any other OIDC token". What was built
   instead is exchange: a service account presents its
   IdP-issued token to `POST /auth/federated` and receives a
   scoped, expiring namespace key, which it then uses
   exactly as any other key. The machinery is issuer-generic
   by construction — anything publishing a JWKS works — and
   an Authentik service account is named as a supported
   source in both the operator and developer guides
   (`docs/operator_guide/authentication.md:142`,
   `docs/developer_guide/authentication.md:418`). Keycloak
   is expected to work identically, since no code is
   issuer-specific, but nothing in the tree or the
   documentation exercises it yet.

   So both halves of what the question actually wants are
   already met. The small-cluster operator who runs no IdP
   keeps namespace keys, which are unchanged and are the
   documented choice for machine credentials. The operator
   who does run one has a path today, and gets it without
   Shaken Fist accepting a foreign token as a bearer
   credential anywhere in the request path.

   What remains is only whether direct acceptance is *also*
   wanted, which is much narrower than the question as
   asked, and is subsumed by open question 13. The rename
   half of the question is open question 11.

7. **Nonce / revocation semantics for OIDC tokens.** Our
   nonce mechanism gives us immediate revocation of
   currently-issued tokens when a key is rotated. OIDC
   has no equivalent at the token level — revocation is
   typically driven by short token lifetimes plus a
   refresh-token flow. SF's response is likely "trust
   the IdP's `exp` and accept that revocation has a
   bounded delay equal to the token lifetime". Need to
   pick a recommended lifetime and document the
   tradeoff.

   **Reframed by auth federation phase 3 (2026-08-12).** The
   premise is now a false dichotomy. The question offers a
   choice between Shaken Fist's nonce and the IdP's `exp`,
   and phase 3 established a third answer: exchange the
   external token for a namespace key and inherit nonce
   revocation for free. That is what the workload half does
   today, and it is why the federated path needed no
   revocation design of its own — deleting the minted key
   invalidates every token derived from it on the next
   request, exactly as for any other key
   (`docs/developer_guide/authentication.md:411-413`).

   So this stops being an independent decision and becomes a
   consequence of one. If human login follows the exchange
   pattern, revocation is already solved and there is
   nothing here to pick. If it authorises directly off the
   IdP's token, then bounded-delay revocation is what the
   choice buys and the recommended lifetime has to be picked
   and documented as this question asks. Open question 13 is
   where that is settled, and it is deliberately not settled
   here.

8. **Inter-node auth.** Today inter-node calls use the
   namespace-key path with short-lived `_service_key*`
   keys. Should inter-node calls move to OIDC?
   Probably not in v1 — SF nodes already have a
   trust-of-cluster relationship that does not benefit
   from federating through an external IdP, and adding
   the IdP to SF's inter-node critical path makes the
   IdP a hard dependency on cluster operation. Likely
   resolution: inter-node stays on the renamed
   service-account-token path; OIDC is opt-in for
   external callers only.

   **Still open, but close to settled (2026-08-12).** The
   premise was re-checked and holds. Inter-node calls still
   mint a short-lived `_service_key_<rand>` key per request
   (`shakenfist/namespace.py:386-410`). Phase 2 moved those
   keys onto the `namespace_keys` tables and gave them
   `sfk_`-format secrets, a five minute key expiry and a
   five minute token, but the mechanism is the one this
   question describes and its likely resolution is now also
   written into this plan's Mission as a constraint and into
   its Agent guidance review checklist.

   The reasoning has if anything strengthened: making an
   external IdP a hard dependency of cluster operation is a
   reliability regression sold as a security improvement,
   and cluster nodes already have a trust relationship that
   federating adds nothing to.

   It is left open rather than closed because closing it
   would foreclose the one thing that would reopen it — a
   deployment where node identity itself comes from
   somewhere else, such as SPIFFE or a cloud provider's
   instance identity document, or an operator who requires
   that every credential in the cluster have a single
   issuer. Note that in that case the answer is a *different*
   issuer for node identity, not the corporate IdP, which is
   a different question from the one asked here.

9. **CLI flow choice.** The SF CLI today is purely
   non-interactive: read a key from a config file, POST
   to `/auth`. OIDC for the CLI means either:
   * **Device code flow** — the CLI prints a URL and a
     code, the user opens it in a browser, comes back,
     CLI now has a refresh token. Works on headless
     boxes. Most natural fit.
   * **Auth code + PKCE with loopback** — CLI opens a
     browser and listens on a random localhost port for
     the redirect. Faster but requires a graphical
     session.
   Likely both, with device code as the default since
   it works everywhere.

   **Still open, and now known to be blocked on something
   the question did not know it needed (2026-08-12).** Both
   flows require the issuer's endpoints — a
   `device_authorization_endpoint` and a `token_endpoint` —
   and a relying party is supposed to learn those from the
   issuer's discovery document. **Discovery was not built.**
   Nothing in the tree fetches
   `.well-known/openid-configuration`; `jwks_uri` is
   operator-supplied and required
   (`shakenfist/schema/trusted_issuer_attributes.py:48-50`),
   which is exactly the right property for validation — the
   key location is never taken from the token being
   validated — and no help at all to a client that has to
   *start* a conversation. Phase 3 built the verification
   half of an OIDC relying party and none of the client
   half. Discovery is therefore a prerequisite of this
   question rather than a detail of it, and has its own row
   in the Execution table.

   The second constraint is where the work lands.
   `sf-client` is in the separate `client-python`
   repository; this repository ships only `sf-ctl` and
   `sf-backup` (`pyproject.toml:157-158`). Neither flow can
   be implemented in this checkout, and the Execution table
   says so per row.

   The lean is unchanged: both, with device code as the
   default because it works on a headless box.

10. **Token caching on the client.** Where does
    `sf-client` cache the OIDC refresh token and access
    token? `~/.shakenfist/oidc-cache` is the obvious
    answer, with file mode 0600. Need to define the
    cache format and invalidation rules.

    **Still open, unchanged in substance (2026-08-12).**
    Nothing built since has touched it. Two things narrow
    it. It is a `client-python` change and cannot be done in
    this repository. And whatever is cached must not defeat
    revocation, which is the property Shaken Fist's
    credential model is built around: a cached refresh token
    is a credential at rest and must be protected like the
    namespace key it replaces, and a cached access token
    must respect its `exp` and must not survive a logout the
    client itself initiated. A cache that quietly extends
    the life of a credential the server considers dead
    undoes the one thing the nonce buys.

11. **Migration of existing namespace keys.** The
    rename to "service account tokens" is mostly
    cosmetic — keys keep working. But the user-facing
    CLI command names (`sf-client namespace add-key`)
    and the JSON shape of `keys` in `namespace_attributes`
    may want to evolve. Need to decide whether the
    rename is a pure UX layer over the existing
    storage or an actual schema migration.

    **Partly resolved by auth federation phase 2, and
    inverted (2026-08-12).** The question asks whether the
    rename is a UX layer or a schema migration. The schema
    migration already happened, for reasons that had nothing
    to do with the rename: keys are first-class objects in
    the `namespace_keys` and `namespace_key_attributes`
    tables (`shakenfist/namespace_key.py:62`), carrying
    expiry, scopes and provenance, and the `keys` JSON
    column on `namespace_attributes` is neither read nor
    written any more (`shakenfist/namespace.py:187-190`).
    The migration is one-shot and idempotent, run by
    `sf-ctl ensure-mariadb-schema`.

    So the expensive half is done and the cheap half is what
    is left. That half is genuinely still open — nothing has
    been renamed — but it is now a question about words and
    command names rather than about storage, and its cost is
    a documentation pass and a deprecation window rather
    than a migration. `sf-client namespace add-key` lives in
    `client-python`, so the CLI surface of any rename lands
    there and not here.

    One clause of the question no longer describes anything
    live. There is no JSON shape of `keys` in
    `namespace_attributes` to evolve; what a caller sees
    comes from `Namespace.keys`, which synthesises the
    legacy `{'nonced_keys': ...}` dict out of the tables for
    the handful of call sites that still read it
    (`shakenfist/namespace.py:197-205`).

12. **Documentation surface.** Three audiences:
    * **Operators** — how to configure a JWKS / issuer
      list, how to wire up Keycloak or Authentik
      end-to-end (worked examples for each), how the
      group claim flows in.
    * **Users / developers** — how to log in via the CLI,
      where the cache lives, how to switch between
      identities.
    * **Architects** — the trust model, why we kept
      service-account tokens, why authorisation stays
      in SF.

    **Still open, and substantially narrowed by auth
    federation phase 4 (2026-08-12).** All three audiences
    now have a document, and all three documents were
    brought current against the as-built code:
    `docs/{developer,operator,user}_guide/authentication.md`,
    plus `docs/glossary.md` and the API reference at
    `docs/developer_guide/api_reference/authentication.md`.
    So this is no longer "write three documents", it is
    "extend five current ones", and it should be planned at
    that size.

    Two of the three audiences are largely served already.
    The architects' view the question names — the trust
    model, why service-account credentials were kept, why
    authorisation stays in Shaken Fist — is covered by the
    glossary and by the developer guide's federated identity
    section. The users' view exists except for the login
    flow that does not exist yet, which is not a
    documentation gap.

    The operators' half is what is genuinely missing, and
    specifically the worked examples. The guides name
    Authentik as a supported issuer but do not walk an
    operator through configuring one, and Keycloak appears
    in the documentation only as an example of a service
    likely to present a private CA certificate
    (`docs/operator_guide/authentication.md:330`).
    End-to-end worked examples for both, including how a
    group claim reaches a namespace grant, remain to be
    written — and they are the part that cannot be written
    until open questions 13 and 14 are settled, because what
    an operator configures depends on which of the two
    session shapes is chosen.

13. **Direct-bearer or exchange-based human sessions?**
    This plan's central unresolved question, and the one
    `PLAN-auth-federation.md` explicitly reserves for this
    plan's own phase 0 decisions pass rather than answering
    in passing. It is posed here with the evidence that has
    accumulated; it is deliberately not answered.

    Two shapes. Under **direct-bearer**, a human completes
    an OIDC flow and the IdP's token becomes the
    `Authorization: Bearer` credential presented to the
    Shaken Fist API; Shaken Fist validates it per request
    and authorises off its claims. This was this plan's
    original design. Under **exchange**, the IdP's token is
    presented once — to `POST /auth/federated` or a
    human-shaped sibling of it — and what the client then
    holds and presents is an ordinary namespace key and the
    access tokens minted from it. That is what the workload
    half already does.

    Four things bear on the choice and they do not all point
    the same way.

    *The missing-`scopes` default is a constraint on
    direct-bearer.* `api_scopes.satisfies()` returns `True`
    unconditionally when the held scope list is `None`
    (`shakenfist/external_api/scopes.py:137-142`), and
    `caller_is_admin` tests the same predicate
    (`shakenfist/external_api/base.py:140-145`). That
    default is correct and deliberate: a token minted before
    the claim existed carries no `scopes`, and refusing
    those would have invalidated every token in flight
    across an upgrade — the reasoning is written down at
    `shakenfist/util/access_tokens.py:31-34`. But an
    IdP-issued token also carries no `scopes` claim, and no
    IdP is ever going to mint one, so under direct-bearer
    such a token would reach `_enforce_scope`
    (`shakenfist/external_api/base.py:1234`) holding `None`
    and satisfy every scope, including `cluster-admin`.

    **This is not a bug and must not be filed as one.** No
    externally-issued token can reach that path today: the
    only way to obtain a Shaken Fist access token is
    `/auth`, which mints its own. It is a constraint the
    direct-bearer option has to answer, and the answer is
    that the missing-claim default would have to become
    issuer-dependent — wildcard for tokens this cluster
    minted, deny for tokens it did not — which is a change
    to the most safety-critical default in the whole
    authorisation path. The backward-compatibility argument
    that justifies the current default applies only to
    tokens Shaken Fist itself minted, because only those
    have a history. Exchange does not have to answer this at
    all: what reaches `_enforce_scope` is always a token
    Shaken Fist minted, carrying the scopes the mapping rule
    granted.

    *Revocation favours exchange.* This is open question 7
    reappearing as a consequence of the choice rather than
    as an independent decision. Exchange inherits the nonce,
    so deleting the minted key invalidates its tokens on the
    next request. Direct-bearer inherits only the IdP's
    `exp`, so revocation acquires a bounded delay equal to
    the token lifetime, the recommendation becomes "keep
    lifetimes short", and token introspection becomes the
    escape hatch if that proves operationally unacceptable.

    *Multi-namespace favours direct-bearer, and cuts the
    other way from everything above.* A human is typically
    in several namespaces. An IdP token can carry a list of
    them in one claim and be authorised against whichever
    namespace each request touches. Every credential Shaken
    Fist mints names exactly one: the JWT identity is
    `<namespace>:<keyname>`
    (`shakenfist/util/access_tokens.py:49`) and a namespace
    key belongs to a single namespace
    (`shakenfist/schema/namespace_key_data.py:60-63`). So
    exchange for a person in five namespaces means five
    exchanges and a client that knows which credential to
    present for which request. Exchange makes the
    multi-namespace problem a client-side one, direct-bearer
    makes it a server-side one, and neither makes it go
    away — see open question 14.

    *Cost is ordinary and mildly favours exchange.*
    Direct-bearer puts signature verification, and a
    possible JWKS refetch, on every request against an
    external dependency; exchange puts one round trip at
    login and afterwards uses a path that is already an
    indexed point read.

    **What would settle it:** two pieces of evidence a phase
    0 pass can actually go and get. First, the real
    multi-namespace distribution — how many namespaces the
    people at a handful of real deployments are actually in
    — because if the answer is usually one then exchange
    wins outright and open question 14 shrinks to a
    convenience, and if it is routinely several then the
    cost of an issuer-dependent missing-claim default has to
    be weighed rather than dodged. Second, a prototype of
    that issuer-dependent default carried far enough to show
    whether it can be expressed *inside* the single existing
    enforcement path rather than beside it, because
    "authorisation lives in one place" is a constraint this
    plan will not trade away.

14. **Multi-namespace authorisation.** Promoted from a
    settled-sounding sentence in an earlier draft of this
    plan to a question of its own, because it is the single
    largest piece of unbuilt work here and was being treated
    as a detail of claim mapping.

    Shaken Fist's authorisation is one namespace deep, by
    construction and everywhere. `parse_jwt_identity()`
    requires exactly two colon-separated components and
    raises otherwise
    (`shakenfist/util/access_tokens.py:68-73`),
    `request_namespace()` returns the first of them as a
    single string
    (`shakenfist/util/access_tokens.py:76`), and fifty-seven
    call sites outside the tests compare that string against
    an object's namespace — seventeen in
    `shakenfist/external_api/artifact.py` alone. Letting a
    caller hold several namespaces at once is not a change
    to a claim reader; it is a change to the shape of every
    authorisation check in the codebase, and each of those
    sites has to be re-read to decide whether it means "the
    caller's namespace", "any namespace the caller holds",
    or "the namespace this object is in".

    There is a second mechanism already in the tree which
    partly answers the same need, and the boundary between
    the two has not been worked through by anybody.
    Namespace [trust](/glossary/#trust) grants visibility
    from trusted namespaces into the trusting namespace
    (`namespace_is_trusted()`,
    `shakenfist/namespace.py:413`), and auth federation
    phase 3 established that scopes compose with trust
    rather than being escaped by it. So a caller holding one
    namespace can already reach objects in another, under an
    explicitly granted relationship, without holding two
    credentials. But trust is administered by the target
    namespace and is a standing property of the deployment,
    whereas a multi-namespace credential would be
    administered in the IdP and would be a property of the
    person. Those are different answers to overlapping
    questions, and shipping both without deciding where the
    line sits gives operators two ways to express one intent
    and no guidance about which to reach for.

    The sub-questions a decisions pass has to answer:
    whether the two-component identity grows a third form or
    is replaced outright; whether a request names its target
    namespace explicitly, as `POST /auth/federated` already
    does, or infers it from the object being touched; what
    `request_namespace()` becomes when there is no single
    answer, and whether it survives at all; and what happens
    to listing endpoints, which today filter by one
    namespace plus its trust relationships.

    **What would settle it:** a mechanical audit of those
    fifty-seven call sites, classifying each into "the
    caller's own namespace", "any namespace the caller
    holds" and "the object's namespace". The classification
    *is* the evidence — if the great majority fall into one
    bucket then the change is a mechanical rename with a
    handful of hand-written exceptions, and if they are
    spread evenly then it is a redesign of the authorisation
    model and has to be sequenced as one. That audit is
    cheap, it is independent of open question 13, and it
    should be done before either question is decided.

## Execution

(Detailed phase plans will be drafted when this plan moves
out of stub status. Phases are tentatively expected to look
like:)

| Phase | Plan | Status |
|-------|------|--------|
| 0. Research and decisions | TBD | Not started |
| 1. JWT validation refactor (split issuance from validation; introduce per-issuer validators) | TBD | Not started |
| 2. OIDC validator (discovery, JWKS fetch + cache, signature + claim verification) | TBD | Not started |
| 3. Claim → namespace authorisation (replace `request_namespace()` with a per-request decision) | TBD | Not started |
| 4. Admin-claim model and `caller_is_admin` rework | TBD | Not started |
| 5. Service-account-token rename of the existing namespace-key surface | TBD | Not started |
| 6. CLI OIDC flows (device code, optionally auth-code-with-PKCE) and token cache | TBD | Not started |
| 7. Worked-example operator docs for Keycloak and Authentik | TBD | Not started |
| 8. Functional test coverage with an in-CI IdP (Keycloak in a container) | TBD | Not started |

This plan is currently in placeholder form. It exists to
record the design direction discussed and to give us a
shared artefact to point at when work begins. None of the
phase plans have been drafted; the open questions above
must be resolved in a phase 0 decisions pass before any
implementation phase is cut.

## Agent guidance

(To be filled in when this plan moves out of stub status.
The structure will mirror `PLAN-network-facade.md`'s
*Agent guidance* section: execution model, planning
effort, step-level guidance table with effort / model /
isolation / brief columns, and the management session
review checklist.)

## Administration and logistics

### Success criteria

When this plan is successfully implemented:

* An operator can configure a cluster to trust one or
  more OIDC issuers (Keycloak and Authentik both work
  with worked examples in `docs/operator_guide/`).
* A human user can `sf-client login` (or equivalent),
  complete an OIDC flow, and from then on `sf-client`
  calls authenticate using the IdP-issued JWT.
* Namespace access for OIDC-authenticated callers is
  driven by claims in the token, with no SF-side
  per-user state required.
* The existing namespace-key mechanism is renamed to
  "service account tokens", still works for automation,
  and is the documented choice for machine credentials.
* The `caller_is_admin` decorator and the privileged
  status of the `system` namespace are driven by a
  claim, not by namespace name alone.
* Inter-node authentication continues to work without
  requiring an external IdP — the IdP is opt-in for
  external callers.
* OIDC validation handles JWKS rotation gracefully
  (cache + refetch on unknown `kid`).
* Audit events (`EVENT_TYPE_AUDIT`) cover OIDC logins
  with at least: issuer, subject, mapped namespaces,
  token id (`jti`).
* The code passes `pre-commit run --all-files`.
* Functional test coverage in `shakenfist/deploy/cluster_ci`
  exercises an end-to-end OIDC login against a
  containerised Keycloak.
* `docs/{developer,operator,user}_guide/authentication.md`
  are updated to describe both the OIDC and
  service-account-token paths and when to use each.

### Future work

* **Per-resource RBAC.** Roles like "read-only on
  namespace X" or "may create instances but not
  networks". Out of scope here; the unit of
  authorisation stays the namespace.
* **Federated trust.** Mapping a single human across
  several IdPs (e.g. internal IdP + partner IdP for
  contractors) onto one logical SF identity. Speculative.
* **Inter-node OIDC.** Move inter-node auth onto OIDC
  too. Deferred because of the IdP-dependency concern
  noted in open question 8.
* **Web console.** A browser UI for SF would naturally
  use the same OIDC flow with auth-code + PKCE. Not in
  scope here, but the auth design should not preclude it.
* **Token introspection / online revocation.** RFC 7662
  introspection or an SF-side revocation list. The v1
  design accepts bounded-delay revocation via short
  token lifetimes; if that proves unacceptable
  operationally, introspection is the next step.

### Bugs fixed during this work

(none yet)

### Documentation index maintenance

When this plan is updated:

* `docs/plans/index.md` — the row for this plan should
  track its overall status. Phase rows are not added.
* `docs/plans/order.yml` — this master plan is registered;
  phase files are not.

### Back brief

Before executing any step of this plan, the implementing
sub-agent must back brief the operator as to its
understanding of the phase plan and how the work it
intends to do aligns with that plan.
