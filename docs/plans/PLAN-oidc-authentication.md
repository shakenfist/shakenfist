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
`AGENTS.md` for build commands, project conventions, and
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
`sf-backup` (`pyproject.toml:190-191`). Anything to do with
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
   `NamespaceKey` DBO (`shakenfist/namespace_key.py:63`)
   living in the `namespace_keys` and
   `namespace_key_attributes` tables, carrying a bcrypt
   hash, a [nonce](/glossary/#nonce), an optional expiry,
   an optional [scope](/glossary/#scope) list and an
   optional provenance dict
   (`shakenfist/schema/namespace_key_attributes.py:25-81`).
   The `keys` JSON column on `namespace_attributes` that
   used to hold them is vestigial — "neither read nor
   written any more"
   (`shakenfist/namespace.py:187-190`). Beware that
   `Namespace.keys` still *synthesises* the legacy
   `{'nonced_keys': ...}` shape out of the tables
   (`shakenfist/namespace.py:206-214`) for the handful of
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
   (`shakenfist/util/access_tokens.py:49-54`). The nonce is
   re-verified against the stored key on every request, so
   rotating or deleting a key immediately invalidates every
   outstanding token minted from it. The default token
   lifetime is fifteen minutes
   (`config.API_TOKEN_DURATION`).
4. **Authentication and scope enforcement are universal.**
   Both run from `Resource.method_decorators`
   (`shakenfist/external_api/base.py:2637-2645`), so a new
   endpoint is authenticated and scope-checked without
   anyone remembering to decorate it. `@api_base.public` is
   the only way out, and the public set is written down and
   individually justified. The scope check itself is
   `_enforce_scope` (`shakenfist/external_api/base.py:2458-2474`),
   comparing the token's `scopes` claim against a scope
   derived from the resource class and the HTTP method.
5. **`caller_is_admin` is two axes, not one.** An
   administrative endpoint now requires **both** the
   `system` namespace and the `cluster-admin` scope
   (`shakenfist/external_api/base.py:279-299`), so a key
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
   (`shakenfist/federation.py:359-376`).
8. **An external identity can already be exchanged for a
   credential.** `POST /auth/federated`
   (`shakenfist/external_api/app.py:645`, handler at
   `shakenfist/external_api/auth.py:1861`) is
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
   (`shakenfist/federation.py:325-352`). JWKS fetching goes
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
    per request (`shakenfist/namespace.py:494-518`). Those
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
  name (`shakenfist/util/access_tokens.py:83`). A human is
  typically in several namespaces. The open questions below
  take this up and answer it: the credential stays one
  namespace deep, and the client holds one per namespace.
* **Nothing on the client side.** `sf-client` lives in the
  separate `client-python` repository and has no login or
  OIDC code; this repo ships only `sf-ctl` and `sf-backup`
  (`pyproject.toml:190-191`). None of phase 3's new route
  families are wrapped by the client either.

### Relationship to the auth federation plan

`PLAN-auth-federation.md` is the *machine* half of
federation and has been largely built: a workload exchanges
an IdP-issued identity token for a scoped namespace key.
This plan is the *human* half. The two share everything up
to and including token validation — trusted issuers, JWKS
fetch and rotation, signature and claim verification — and
this plan should add no second copy of any of it.

Where they might have diverged is what happens after
validation. The federation plan mints a key and lets every
existing consumer carry on unchanged. This plan's original
design authorised requests directly off the external token.
They do not diverge: open question 13 below settled human
login onto the exchange pattern too, in the phase 0 decisions
pass the auth federation plan reserved it for rather than
answering it in passing.

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
  exactly one, a login mints the set of credentials the
  person is entitled to rather than one credential naming
  several namespaces — open question 14.
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
   `/auth/issuers` (`shakenfist/external_api/app.py:638-640`).
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
   coercion (`shakenfist/federation.py:359-376`). So the
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
   (`shakenfist/external_api/base.py:2637-2645`) rather than
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
   (`shakenfist/util/access_tokens.py:49-54`). `scopes` is
   the interesting one, because it is precisely the claim an
   IdP-issued token will not have — see open question 13.

   What has **not** happened is the second half.
   `request_namespace()` is still a string split over a
   two-component identity
   (`shakenfist/util/access_tokens.py:75-80`), called from
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
   (`shakenfist/federation.py:325-352`, with
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
   (`shakenfist/external_api/base.py:279-299`), so the
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

   **Resolved by phase 0 (2026-11-07): keep both axes.**
   `caller_is_admin` continues to require both the `system`
   namespace and the `cluster-admin` scope
   (`shakenfist/external_api/base.py:279-299`). The namespace
   half is not dropped.

   **This reverses the expectation the question was written
   with**, and it is contingent on open question 14. Step 0a's
   call-site audit established that dropping the namespace half
   is a *prerequisite* of a multi-namespace credential rather
   than a companion to it: the moment a credential can name
   several namespaces, "holds `system`" comes to mean "satisfies
   `caller_is_admin` on every request, including requests that
   touch only a tenant namespace", and the namespace half has to
   go to stop that escalation. Open question 14 is answered no,
   so a credential names exactly one namespace, "holds `system`"
   stays as unambiguous as it is today, and the escalation
   cannot arise. There is nothing left for the change to fix,
   and a check which requires both a bootstrap namespace and an
   explicit scope is the stronger of the two.

   If a future plan reopens question 14, this question reopens
   with it, and on step 0a's evidence the answer then is to drop
   the namespace half. It does not reopen on its own: the
   namespace half costs nothing while a credential is one
   namespace deep.

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
   (`docs/operator_guide/authentication.md:143-144`,
   `docs/developer_guide/authentication.md:429`). Keycloak
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
   (`docs/developer_guide/authentication.md:422-424`).

   So this stops being an independent decision and becomes a
   consequence of one. If human login follows the exchange
   pattern, revocation is already solved and there is
   nothing here to pick. If it authorises directly off the
   IdP's token, then bounded-delay revocation is what the
   choice buys and the recommended lifetime has to be picked
   and documented as this question asks. Open question 13 is
   where that is settled, and it is deliberately not settled
   here.

   **Resolved by phase 0 (2026-11-07), as a consequence of open
   question 13 rather than as a choice of its own: there is
   nothing here to pick.** Human login follows the exchange
   pattern, so what reaches the request path is a Shaken Fist
   access token bound to a namespace key's nonce. Deleting the
   minted key invalidates every token derived from it on the
   next request, exactly as for any other key. No bounded-delay
   revocation is introduced, and no recommended token lifetime
   has to be chosen or documented. The IdP's `exp` bounds only
   the window in which an identity token may be exchanged, which
   is a different and much smaller thing than a window in which
   a revoked credential still works.

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
   (`shakenfist/namespace.py:494-518`). Phase 2 moved those
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
   `sf-backup` (`pyproject.toml:190-191`). Neither flow can
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
    tables (`shakenfist/namespace_key.py:63`), carrying
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
    (`shakenfist/namespace.py:206-214`).

    **Resolved by phase 0 (2026-11-07): no rename, and phase 6
    is repurposed.** Namespace keys keep their name and remain
    the documented machine credential. The expensive half of
    this question — storage — already happened for unrelated
    reasons, so a rename would buy vocabulary at the price of a
    documentation pass and a deprecation window across two
    repositories, and would leave every operator who has read
    the current guides holding the old word.

    Phase 6 does not disappear, because there is something more
    useful to do with it. The key metadata is already modelled
    and is served nowhere: `NamespaceKey.external_view()`
    (`shakenfist/namespace_key.py:404-418`) produces
    `namespace`, `name`, `expiry`, `scopes` and `provenance`,
    while `AuthNamespaceKeysEndpoint.get`
    (`shakenfist/external_api/auth.py:591-595`) still returns
    bare key names out of the legacy `nonced_keys` compatibility
    view (`shakenfist/namespace.py:206-214`). That is issue
    #3672, and it is what actually blocks operating a key as a
    service-account credential: a credential whose expiry and
    scopes cannot be read back cannot be audited or rotated on
    purpose, whatever it is called. Phase 6 becomes that work.

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
    (`docs/operator_guide/authentication.md:384`).
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
    (`shakenfist/external_api/base.py:291-292`). That
    default is correct and deliberate: a token minted before
    the claim existed carries no `scopes`, and refusing
    those would have invalidated every token in flight
    across an upgrade — the reasoning is written down at
    `shakenfist/util/access_tokens.py:32-35`. But an
    IdP-issued token also carries no `scopes` claim, and no
    IdP is ever going to mint one, so under direct-bearer
    such a token would reach `_enforce_scope`
    (`shakenfist/external_api/base.py:2461-2464`) holding `None`
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
    (`shakenfist/util/access_tokens.py:56`) and a namespace
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

    **The first of those two is not in fact obtainable, and
    phase 0's planning pass established that rather than
    discovering it halfway through (2026-11-07).** The
    distribution was measured against sfcbr, the only real
    deployment there is: sixteen namespaces, twelve of them
    ephemeral per-CI-job `sfcbr-*`, and exactly one key in the
    whole cluster held by a person (`roberto`, in `system`). A
    sample of size one, drawn from a cluster that has no staff
    because it is a CI cloud, cannot discriminate between the
    two options — and it must not be read as "usually one",
    which is the reading that would hand exchange the win. So
    the weight falls on the second piece of evidence and on open
    question 14's call-site audit, both of which are facts about
    our own code rather than about other people's deployments.
    See
    [phase 0](PLAN-oidc-authentication-phase-00-decisions.md),
    decisions D2 and D3.

    The same measurement turned up something this question did
    not ask for, and it bears on open question 14. The shape the
    question was trying to detect is already in production, for
    a machine: the CI conductor is one actor holding a separate
    credential in every namespace it works in, all of them named
    `conductor`, because a namespace key can name exactly one
    namespace. The count is whatever the job population happens
    to be -- fourteen in one sample, eight an hour later -- so
    it is the per-namespace invariant that matters here, not the
    number. That is this question's own stated
    cost of exchange — several exchanges and a client that knows
    which credential to present — being paid today. Meanwhile
    `ci-images` takes the other route for the same need, listing
    every live job namespace in its
    [trust](/glossary/#trust) list so jobs can read shared
    images with no second credential at all. Both mechanisms are
    live side by side, serving one need, chosen differently in
    two places by the same operator.

    **Resolved by phase 0 (2026-11-07): exchange, not
    direct-bearer.** A human completes an OIDC flow and
    exchanges the resulting identity token for an ordinary
    namespace key, and that key — with the access tokens minted
    from it — is what the client holds and what every request
    presents. This is the shape the workload half already has,
    so the human half adds a flow rather than a second
    authorisation path.

    The reason is not that three of the four considerations
    above favour exchange. Counting considerations would be
    reasoning from the absence that D2 and D3 of
    [phase 0](PLAN-oidc-authentication-phase-00-decisions.md)
    refuse to reason from. The reason is that the single
    consideration which favours direct-bearer —
    multi-namespace — **provably evaporates when it is built.**
    Step 0b's probe got direct-bearer working through the
    existing enforcement path with no change to any downstream
    reader, and it fitted only by forging `sub` as
    `<namespace>:<keyname>`, derived per request by evaluating
    mapping rules against the token's claims.
    `parse_jwt_identity()` demands exactly two colon-separated
    components (`shakenfist/util/access_tokens.py:75-80`), so an
    IdP subject cannot be carried through untouched: the probe
    measured `alice@example.com` and GitHub's `repo:x:ref:y`
    both failing there, and a validly signed foreign token
    answering 500 as a result. With the forgery in place
    `request_namespace()` returns one string, measured with
    matching rules live in two namespaces at once. So
    direct-bearer destroys the IdP's identity in order to make
    the request parseable at all, and buys exactly one namespace
    per request for doing it.

    **The finding most worth carrying forward is
    counter-intuitive, and it is about the missing-`scopes`
    default rather than about either option: the
    issuer-dependent default this question proposes as
    direct-bearer's answer cannot be expressed as a change to
    `api_scopes.satisfies()` at all.** There are three
    enforcement sites, not the two named above, and the third is
    a cap rather than a grant. `caller_scopes()`
    (`shakenfist/external_api/base.py:302-312`) tests
    `held is None` itself and returns `None`, and the
    mapping-rule scope cap
    (`shakenfist/external_api/auth.py:1109-1118`) reads
    `caller_scopes()` and short-circuits on `None` without ever
    consulting `satisfies()`. Hardening `satisfies()` therefore
    leaves that cap lifted while `caller_is_admin`
    (`shakenfist/external_api/base.py:292`) and `_enforce_scope`
    (`shakenfist/external_api/base.py:2464`) deny — a fail-open
    at the one site whose whole job is to stop a caller
    authoring a standing instruction to mint wildcard keys in
    `system`. Normalising the claim to `[]` at the boundary
    swings the other way: `_enforce_scope` refuses `rule.write`
    one layer earlier, so an IdP-authenticated administrator
    could author no mapping rule at all — not a wildcard one,
    not a narrow one — and the refusal reads `token is not
    scoped for this operation`, which says nothing about the
    default that caused it. `None` is a grant at two sites and
    the absence of a cap at the third, and one default cannot
    serve both. Both halves of that were measured through real
    requests in step 0b rather than reasoned about.

    The only default which makes all three sites coherent is to
    synthesise a real scope set from the mapping rule that
    authenticated the request — at which point direct-bearer is
    exchange without persistence, minus the nonce and minus an
    audit row naming a credential that exists. That is the sense
    in which the Mission's "authorisation lives in one place"
    survives direct-bearer: it survives by making direct-bearer
    indistinguishable from exchange at the point of enforcement.

    The supporting costs are smaller than that one, and they
    point the same way:

    * Every mechanism which populates the request context
      *while keeping `federation.validate_token()` in the path*
      needs private or undocumented behaviour. There is no
      public "set the verified token for this request" API in
      the installed 4.7.4, so the choice is four undocumented
      `flask.g._jwt_extended_*` attributes, or an undocumented
      guarantee that the dict `verify_jwt_in_request()` returns
      is the same object `get_jwt()` later reads.
    * The one public route, `@jwt.decode_key_loader`, loses the
      per-issuer audience binding, because the decode settings
      are application-global. Demonstrated rather than deduced:
      a token minted for another audience verified through
      `verify_jwt_in_request()` while
      `federation.validate_token()` refused it, and so did a
      token carrying no `exp` at all.
    * Audit events would name a key which does not exist.
      `log_token_use` (`shakenfist/external_api/base.py:1511`)
      recorded the forged namespace and key name, and the IdP's
      subject appeared in no event.
    * There is no nonce, so revocation degrades from immediate to
      the token lifetime. That is open question 7, which this
      answer therefore settles.

    One property of the tree today belongs with those, because
    it is a cost direct-bearer must pay and exchange does not.
    `federation.TokenValidationFailed` is not caught by
    `handle_authorization_exceptions`
    (`shakenfist/external_api/base.py:2192`), which catches only
    `flask_jwt_extended` and PyJWT classes, so a foreign token
    which fails validation inside the request path answers
    **500**. The exchange endpoint is unaffected because it
    handles that exception in its own handler
    (`shakenfist/external_api/auth.py:1962-1964`); anything
    validating a foreign token inside the authorisation path
    owes an error-translation layer before it owes anything
    else.

    What this constrains downstream. Phase 3's discovery work is
    untouched: a human still has to start a conversation with
    the issuer, whichever credential the conversation ends in.
    Phase 7 caches namespace keys and the access tokens minted
    from them rather than an IdP refresh token, which narrows
    what open question 10 must protect without making it easier.
    Phase 8's worked examples walk an operator through an
    exchange, so what a human login asks an operator to
    configure is the trusted issuers and mapping rules a
    workload exchange already needs. Open question 6's residue
    — whether direct acceptance of a foreign bearer token is
    *also* wanted — is answered no for this plan. And the
    token-introspection entry under Future work has no work in
    it.

14. **Multi-namespace authorisation.** Promoted from a
    settled-sounding sentence in an earlier draft of this
    plan to a question of its own, because it is the single
    largest piece of unbuilt work here and was being treated
    as a detail of claim mapping.

    Shaken Fist's authorisation is one namespace deep, by
    construction and everywhere. `parse_jwt_identity()`
    requires exactly two colon-separated components and
    raises otherwise
    (`shakenfist/util/access_tokens.py:75-80`),
    `request_namespace()` returns the first of them as a
    single string
    (`shakenfist/util/access_tokens.py:83`), and fifty-seven
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
    `shakenfist/namespace.py:521-531`), and auth federation
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

    **Resolved by phase 0 (2026-11-07): no. A credential stays
    one namespace deep, and the multiplicity moves to the
    client.** A login mints the set of credentials the person is
    entitled to, one namespace key per namespace, and the client
    holds that set and presents the right member for each
    request. Server-side authorisation does not change:
    `parse_jwt_identity()` keeps its two components,
    `request_namespace()` keeps returning one string
    (`shakenfist/util/access_tokens.py:83`), and none of the
    fifty-seven call sites move. This is what the CI conductor
    already does in production — one actor holding a `conductor`
    key in each namespace it works in, eight of them when step
    0c sampled — so the client-side shape is in service today
    for a machine, and what a person lacks is the tooling rather
    than the model.

    **This is a refusal of an affordable change, so it owes a
    reason which is not cost.** Step 0a's audit found the
    server-side change would be mechanical rather than a
    redesign: thirty-nine of the fifty-five executable call
    sites are entitlement tests whose single-string comparison
    becomes a set-membership test, and they are not thirty-nine
    edits but three helper signatures —
    `namespace_is_trusted()`, `namespace_filter` and
    `namespace_or_shared_filter` — plus one ownership idiom
    repeated eleven times. Affordable is not a reason to do it.
    Three things decide it the other way.

    1. **It would make `caller_is_admin` an escalation.** A
       credential naming `system` as well as a tenant namespace
       satisfies `caller_is_admin`
       (`shakenfist/external_api/base.py:279-299`) on *every*
       request, including requests which touch only the tenant,
       because the test becomes "does the held set contain
       `system`". Eleven other sites share that shape, and two
       of them — `shakenfist/blob.py:299` and
       `shakenfist/schema/object_reference.py:182` — are
       field-level disclosure inside `external_view()`, so a
       dual-namespace caller would start seeing blob locations
       and node references in every object it rendered. Being in
       two namespaces at once should confer no extra authority
       over either, and today it cannot.
    2. **A proxy hop would silently drop entitlements.** Four
       sites *write* `request_namespace()` into a freshly minted
       single-namespace `_service_key` for the next node —
       `shakenfist/external_api/artifact.py:604`,
       `shakenfist/external_api/base.py:1763`,
       `shakenfist/external_api/base.py:1993` and
       `shakenfist/external_api/blob.py:61`, all through
       `get_api_token()` (`shakenfist/namespace.py:494-518`). A
       multi-namespace request which crossed a node hop would be
       re-authenticated as a single namespace on the far side
       and would lose entitlements mid-request, with no error.
       The audit found this; this plan had not.
    3. **Trust already serves the cross-namespace need**, which
       is the next three paragraphs.

    **Where the line sits between trust and a multi-namespace
    credential.** The two do not overlap, and what separates
    them is what each one grants.

    [Trust](/glossary/#trust) is a standing, deployment-level
    grant of visibility *into* a namespace. It is administered by
    the namespace being read, it applies to everyone that
    namespace trusts rather than to a named person, and it
    confers no ability to act as the trusting namespace. An
    operator reaches for it when several actors need to see one
    namespace's objects: `ci-images` listing every live job
    namespace so that CI jobs can read shared images is the
    production example, and it is why those jobs hold no second
    credential.

    A multi-credential login is a per-person *entitlement to act
    as* a namespace. It is administered in the IdP, as a
    consequence of group membership, it is a property of the
    person rather than of the deployment, and it carries the full
    authority of each namespace it names. An operator reaches
    for it when a person must create, modify and delete in
    several namespaces.

    One grants visibility; the other grants identity. An
    operator choosing between them should also know that trust
    composes into its own administration:
    `requires_namespace_ownership` gates on
    `namespace_is_trusted()`
    (`shakenfist/external_api/auth.py:101`), and
    `AuthNamespaceTrustsEndpoint.post` and
    `AuthNamespaceTrustEndpoint.delete`
    (`shakenfist/external_api/auth.py:803`,
    `shakenfist/external_api/auth.py:832`) re-run that same
    owner-or-trusted test, so a namespace granted trust can
    grant trust onward. Step 0a found that. It is a property of
    trust rather than a defect this answer repairs, and it is a
    reason to prefer the narrower mechanism when either would
    serve.

    One caveat on reaching for trust, from step 0c's sample:
    `ci-images` trusts a namespace which no longer has a row,
    and that entry outlived the revocation sweep `09e6dcdb56`
    added by at least tens of minutes while every other entry
    beside it churned. A standing grant administered by the
    namespace being read needs removing when its subject goes
    away. That is not a reason to prefer a credential set, which
    has the same problem with the IdP owning it instead, but it
    is a reason the operator guidance phase 8 writes should tell
    an operator to audit a trust list. Filed as #4829: the
    revocation sweep is forward-only and nothing backfilled the
    residue, so entries written before 2026-09-12 survive.

    The sub-questions this question lists therefore answer
    themselves. The two-component identity neither grows a third
    form nor is replaced. A request names its target namespace
    exactly as it does today, because the credential presented
    names it. `request_namespace()` survives unchanged. Listing
    endpoints keep filtering by one namespace plus its trust
    relationships, and a person in five namespaces lists five
    times.

    What this constrains downstream. Phase 4 stops being a
    server-side authorisation change and becomes the minting of
    the entitled set at login. Phase 7 absorbs the multiplicity:
    the client holds a set of credentials and chooses between
    them per request, which is new work there and none here.
    Open question 5 loses its premise and is answered
    accordingly. And open question 2's human half closes here —
    a claim grants into several namespaces by matching a mapping
    rule in each of them, one rule per namespace, which is the
    object model that already exists.

## Execution

Phase 0 ran first, as it had to. It settles open questions
13 and 14, and what most of the rows below actually *are*
depended on which way 13 went. It went to exchange, and 14
to one namespace per credential, which rewrote phases 4, 6
and 7 and abandoned phase 5.

The `file:line` references throughout this plan were re-resolved
against the tree at commit `6b0ab81b2a` (phase 0 step 0e), so they
are true as of that commit and may drift as the code moves.

The **Repo** column is not decoration. `sf-client` lives in
the separate `client-python` repository; this repository
ships only `sf-ctl` and `sf-backup`
(`pyproject.toml:190-191`). A phase that adds a login
command therefore cannot be executed in this checkout at
all, and an agent handed such a brief in the wrong tree
finds that out only after it has read the codebase. Every
row names the repository its work lands in, and work does
not land anywhere else.

| Phase | Repo | Plan | Status | Merged |
|-------|------|------|--------|--------|
| 0. Research and decisions | shakenfist | [PLAN-oidc-authentication-phase-00-decisions.md](PLAN-oidc-authentication-phase-00-decisions.md) | Complete | `2bbe472eff` (#4831) |
| 1. JWT validation refactor (split issuance from validation; introduce per-issuer validators) | shakenfist | [PLAN-auth-federation-phase-03-exchange.md](PLAN-auth-federation-phase-03-exchange.md) | Superseded | Superseded -- landed as `PLAN-auth-federation.md` phase 3 steps 3a-3c and 3f: `72187d2ff` (#3556), `c64269e63` (#3625) |
| 2. OIDC validator (discovery, JWKS fetch + cache, signature + claim verification) | shakenfist | [PLAN-auth-federation-phase-03-exchange.md](PLAN-auth-federation-phase-03-exchange.md) | Superseded | Superseded -- landed as `PLAN-auth-federation.md` phase 3 step 3f: `c64269e63` (#3625), `1e7a167ea` (#3641) |
| 3. OIDC discovery | shakenfist | [PLAN-oidc-authentication-phase-03-discovery.md](PLAN-oidc-authentication-phase-03-discovery.md) | In progress | — |
| 4. Minting the entitled set of credentials at login | shakenfist | TBD | Not started | — |
| 5. Admin as a claim | shakenfist | TBD | Abandoned | — |
| 6. Serving namespace key metadata (#3672) | shakenfist, client-python | TBD | Not started | — |
| 7. Interactive CLI flows, the credential set and the token cache | client-python | TBD | Not started | — |
| 8. Worked operator examples for Keycloak and Authentik | shakenfist | TBD | Not started | — |
| 9. Functional coverage against a containerised IdP | shakenfist | TBD | Not started | — |
| 10. Push audit | shakenfist | PLAN-oidc-authentication-phase-10-push-audit.md | Not started | — |

The `Merged` column records what put each phase on `develop`:
the merge commit of its pull request, or an explicit
`first..last` range where the phase landed directly. It is
filled in as each phase lands, so `—` means the phase has not
landed yet. Phases 1 and 2 are the exception: they were
superseded rather than abandoned, so their cells name the
merges that landed the work under another plan, and phase
10's audit reads those ranges directly rather than following
a pointer. `PLAN-auth-federation.md` is `Complete` and so
carries no `Merged` column of its own -- a Complete plan is
not reopened to acquire one -- which is why the merges are
written out here. They were reconstructed from that plan's
phase 3 step table, whose per-step commit subjects were
matched against the first-parent history rather than a
path-filtered `git log`: #3556 landed steps 3a-3d, #3625
landed 3e-3i, and #3641 added trust anchors for a private
JWKS endpoint.

**Phase 0 — research and decisions.** Settles open question
13, direct-bearer versus exchange-based human sessions, and
open question 14, multi-namespace authorisation. Both are
answered above, as of 2026-11-07: exchange, and one
namespace per credential with the multiplicity held by the
client. The call-site audit open question 14 asks for was
the cheapest useful work in the plan and was independent of
13, so it happened here regardless of how 13 was argued, and
it is what decided 14. Question 13 gated questions 5, 6 and
7, which are answered alongside it, and question 12, which
phase 8 can now be planned against.

**Phases 1 and 2 — superseded, and kept on purpose.** Both
were built as auth federation phase 3, in
`shakenfist/federation.py`. Phase 1's split of issuance from
validation is there in the form of a separate module with a
pinned RS/ES/PS algorithm allowlist and HS deliberately
absent (`shakenfist/federation.py:39-51`); phase 2's JWKS
fetch, caching and rotation are `JWKSCache`
(`shakenfist/federation.py:120-210`), and its signature and
claim verification is the decode call that requires `exp`,
`iss` and `aud` and allows zero clock leeway
(`shakenfist/federation.py:325-352`). The rows are struck
rather than deleted so that a reader six months from now
can see that this work was done and where, instead of
concluding from a shorter table that it is still to do.

One half of phase 2 was **not** built. Nothing in the tree
fetches `.well-known/openid-configuration`; `jwks_uri` is
operator-supplied and required. That half survives as phase
3 below rather than dying with the row it was written in.

**Phase 3 — OIDC discovery.** New, and a prerequisite rather
than a convenience. Phase 3 of the federation plan built the
verification half of a relying party and none of the client
half: a workload arrives already holding a minted token, so
nothing ever needed to *start* a conversation with an
issuer. A human client does, and the endpoints that
conversation needs — `device_authorization_endpoint`,
`token_endpoint` — are exactly what a discovery document
publishes. Open question 9 is blocked on this, and so
therefore is phase 7. The security property that the JWKS
location is never taken from the token being validated is
not up for discussion here: discovery populates a
`TrustedIssuer`'s fields at configuration time, and
validation keeps reading them from the object.

Two things a client flow needs are **not** in any discovery
document, and this section did not previously say so: the
`client_id` registered at the provider, and the OIDC scope
list to request, without which a group claim never appears
in a token at all. Both are operator-supplied, both need a
column on the issuer object, and a phase executing in
`client-python` cannot add a column to a table in this
repository -- so they land here.

The endpoint which serves this metadata lands here for the
same reason. `/auth/issuers` is administrator-only, and a
person running a login command holds no Shaken Fist
credential yet by definition, so the metadata has to reach
them from an unauthenticated endpoint in this repository.
Phase 3's plan records the argument as its decision D7 and
gates that step on a back brief, because it is a scope
judgement rather than a design one.

**Phase 4 — minting the entitled set of credentials at
login.** Open question 14 is answered no, so this is no
longer where `request_namespace()` stops being a string
split: it stays a string split
(`shakenfist/util/access_tokens.py:83`), and so does every
one of the fifty-seven sites that read it. What the phase
builds instead is the server half of a human login — match
the identity token's claims against the mapping rules of
each namespace which has one, and mint one namespace key per
match, so that a person's entitlements come back as a set
from one exchange rather than from one exchange per
namespace. Open question 2's human half closes here. The
audit in
[phase 0](PLAN-oidc-authentication-phase-00-decisions.md) is
what sized this phase, and it also fixes its boundary: each
minted credential still names exactly one namespace.

**Phase 5 — admin as a claim, declined.** Half of it was
done already: `caller_is_admin` requires the `cluster-admin`
scope as well as the `system` namespace
(`shakenfist/external_api/base.py:279-299`). The other half
— dropping the namespace half so that holding `cluster-admin`
is sufficient — is refused by open question 5, which keeps
both axes, so the phase has no content left.

The row is marked `Abandoned` rather than deleted. Phase 6's
note used to instruct that a "no" answer should make a row
disappear rather than shrink, and the reasoning behind that
was that a shrunken row reads as unfinished work. A status of
`Abandoned` does not: the `plan-status-vocabulary` shared
block in `PLAN-TEMPLATE.md` has that term for work
deliberately dropped without being done, which is exactly
what this is. Keeping the row also leaves phases 6 to 10
numbered as this document, its phase plans and
`docs/plans/index.md` already cite them. The decision is
contingent on open question 14: if a credential is ever
allowed to name several namespaces, this phase comes back,
because the namespace half then has to go.

**Phase 6 — serving namespace key metadata.** Open question
11 is answered "no rename", and rather than disappearing the
row is repurposed onto the work that answer exposed as the
real obstacle. Keys keep their name; what they do not have
is an API which serves their properties.
`NamespaceKey.external_view()`
(`shakenfist/namespace_key.py:404-418`) already produces
`namespace`, `name`, `expiry`, `scopes` and `provenance`,
and `AuthNamespaceKeysEndpoint.get`
(`shakenfist/external_api/auth.py:591-595`) still returns
bare names out of the legacy `nonced_keys` compatibility
view. That is issue #3672, recorded against this row because
it is what blocks operating a key as a service-account
credential: a credential whose expiry and scopes cannot be
read back cannot be audited or rotated on purpose. The row
still names two repositories — the endpoint is this
repository's, and whatever renders the new fields in
`sf-client namespace show` is `client-python`'s.

**Phase 7 — interactive CLI flows, the credential set and
the token cache.** Device code, and authorisation code with
PKCE where a browser is available, plus the credential cache
open question 10 describes. Entirely `client-python`: none of
it can be implemented in this repository. Depends on phase 3,
because neither flow can begin without the issuer's
endpoints, and on phase 4, because what the flow returns is
the set of credentials the person is entitled to.

Open question 14's multiplicity lands here. The client holds
one namespace key per namespace and presents the right one
for each request, which is what the CI conductor's
provisioning automation already does for a machine and what
a person has no tooling for at all. So the cache holds a set
rather than a credential, every member of it is a credential
at rest, and choosing between them is this phase's problem
rather than the server's.

**Phase 8 — worked operator examples.** Care is needed about
what is genuinely new here. The operator guide already has
an issuer-configuration section and a worked GitHub Actions
example (`docs/operator_guide/authentication.md:203`), and
Authentik is named in both the operator and developer guides
as a supported source for the *workload exchange*
(`docs/operator_guide/authentication.md:143-144`,
`docs/developer_guide/authentication.md:429`). Keycloak
appears in the documentation exactly once, as an example of
a service likely to present a private CA certificate
(`docs/operator_guide/authentication.md:384`), and nothing
in the tree exercises it. So what this phase owes is: a
first end-to-end walkthrough of configuring either provider,
for a *human* login rather than a workload exchange, for
both Keycloak and Authentik, including how a group claim
reaches a namespace grant. Questions 13 and 14 are settled,
so the session shape the walkthrough documents is known: an
exchange which mints one namespace key per namespace the
person is entitled to.

**Phase 9 — functional coverage against a containerised
IdP.** The federation exchange already has functional
coverage in `test_federation.py` under
`shakenfist/deploy/shakenfist_ci/cluster_ci_tests/`, but
against a throwaway in-process JWKS server rather than a
real provider — which is the right trade for testing
validation and no use at all for testing a flow. This phase
stands up a real IdP in a container and drives a login
through it headlessly, which is also the only honest test of
phase 3's discovery and of phase 8's worked examples.

**Phase 10 — push audit.** Runs `PUSH-AUDIT.md` over the
accumulated diff of every phase in this plan against
`develop`, not the last phase's diff alone. Findings land as
their own pull request, and the plan is not complete until
each is resolved or declined in writing here. If the audit
finds nothing, that is recorded in one sentence.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making. The workflow, effort
levels, model choice guidance, and review checklist follow
`PLAN-TEMPLATE.md` exactly; each phase plan carries its own
step-level table (Step / Effort / Model / Isolation /
Brief).

Two things are specific to this plan. First, a brief must
state which repository its work lands in, matching the
Execution table's Repo column, and a sub-agent working on a
`client-python` phase is given a checkout of that repository
rather than this one. A brief that names a file this
repository does not contain is a defect in the brief, not a
puzzle for the agent to solve.

Second, phase 0 is a decisions pass and produces no code at
all. Its output is answers to open questions 13 and 14
written back into this document, with the evidence that
produced them. A phase 0 that arrives with an implementation
attached has skipped the part that mattered.

### Planning effort

* Phase 0 (research and decisions): **high** — it settles a
  security-architecture question that every later phase is
  shaped by, and the evidence it needs (a real
  multi-namespace distribution, a call-site audit, a
  prototype of an issuer-dependent missing-claim default)
  has to be gathered rather than reasoned about.
* Phase 3 (discovery): **medium** — a well-specified
  protocol and one new fetch path, with the security
  properties already settled by the existing issuer model.
  Review at high effort anyway: it adds a network fetch
  driven by operator-supplied URLs.
* Phase 4 (minting the entitled credential set): **high** —
  it is where an identity becomes a set of credentials, and
  phase 0's answer to open question 14 means it must mint
  several single-namespace keys without ever minting one into
  `system` as a side effect. Review at high effort: it is the
  cluster's privilege boundary, and the failure mode is
  silent.
* Phase 6 (serving key metadata): **medium** — one endpoint
  serving a view that already exists, plus whatever renders
  it client-side. No storage work and no rename; both of
  those were settled before the phase was cut.
* Phase 7 (CLI flows and token cache): **high** — two OAuth
  flows, a credential at rest, and a cache whose
  invalidation rules must not outlive the server's view of
  the credential.
* Phase 8 (worked operator examples): **medium**, reviewed
  at **high** — the examples must be produced by actually
  configuring the two providers, not written from memory of
  how they work. A plausible-looking walkthrough that does
  not run is worse than no walkthrough.
* Phase 9 (functional coverage): **high** — CI topology,
  a containerised provider, and driving an interactive flow
  headlessly.

### Management session review checklist

As per `PLAN-TEMPLATE.md`, plus for this plan specifically:

- [ ] No phase weakens `api_scopes.satisfies()`'s treatment
      of a missing `scopes` claim as a wildcard
      (`shakenfist/external_api/scopes.py:137-142`) without
      saying so explicitly and justifying it. That default
      is safe for tokens Shaken Fist minted, because only
      those have a history, and is not obviously safe for
      tokens it did not — see open question 13.
- [ ] Inter-node authentication is never put behind an
      external IdP (`shakenfist/namespace.py:494-518`).
      Making the IdP a hard dependency of cluster operation
      is a reliability regression, not a security
      improvement.
- [ ] Authorisation still happens in exactly one place. No
      phase adds a second path beside
      `Resource.method_decorators`
      (`shakenfist/external_api/base.py:2637`), whatever
      open question 13 decides.
- [ ] No second copy of JWT validation, JWKS fetching or
      caching. Anything new extends
      `shakenfist/federation.py` rather than sitting beside
      it.
- [ ] `jwks_uri` is still never taken from the token being
      validated. Discovery populates a `TrustedIssuer`'s
      fields at configuration time; validation keeps reading
      them from the object.
- [ ] No credential material — identity tokens, refresh
      tokens, minted secrets — is written to events, logs,
      fixtures or a client-side cache in the clear. The
      token cache of phase 7 is a new place for this to go
      wrong and the federation plan's experience says it
      will be tried.
- [ ] Claim matching is still exact: an exact string, or
      membership of a list of exact strings, with no
      globbing, regular expressions, prefix matching or
      coercion (`shakenfist/federation.py:359-376`). If a
      phase wants pattern matching it argues for it in the
      open, because a claim matcher that is nearly right is
      an authorisation bypass.
- [ ] A cluster with no trusted issuers configured behaves
      exactly as it does today, proven by tests that
      pre-date the change.
- [ ] The diff lands in the repository the phase's Execution
      row names. A `shakenfist` row whose diff touches
      `client-python`, or the reverse, is a failed step
      rather than a bonus.

## Administration and logistics

### Success criteria

When this plan is successfully implemented:

* An operator can take a Keycloak or an Authentik
  deployment from nothing to a human logging in, by
  following a worked example in
  `docs/operator_guide/authentication.md`. Trusting an
  issuer is already configuration rather than code
  (`shakenfist/trusted_issuer.py:41`); what is new is the
  end-to-end walkthrough for a *human* login, which today
  exists only for the GitHub Actions workload path.
* A human user can run a login command in `sf-client`,
  complete an OIDC flow, and thereafter make API calls
  without holding a static key in a file. What the client
  presents afterwards is a namespace key exchanged for the
  IdP's token, one per namespace the person is entitled to:
  open question 13 chose exchange over direct-bearer. The
  command itself lands in `client-python`.
* Namespace access for OIDC-authenticated humans is driven
  by claims in the token, with no per-person state in
  Shaken Fist — mapping rules are per-grant, not per-user —
  and the multi-namespace case of open question 14 is
  answered rather than deferred again.
* Namespace keys still work for automation and are the
  documented choice for machine credentials on a cluster
  that runs no IdP, under that name: open question 11
  declined the rename. What they gain instead is an API
  which serves their expiry, scopes and provenance, which is
  issue #3672.
* The privileged status of the `system` namespace is
  settled, and open question 5 settles it: the two-axis
  check (`shakenfist/external_api/base.py:279-299`) is kept,
  because a credential names exactly one namespace and
  "holds `system`" is therefore unambiguous. Dropping the
  namespace half is what a multi-namespace credential would
  have required, and open question 14 declined that.
* Inter-node authentication continues to work without
  requiring an external IdP — the IdP is opt-in for
  external callers.
* JWKS caching and rotation still work as auth federation
  phase 3 built them
  (`shakenfist/federation.py:120-210`): cached per issuer,
  refetched once on an unknown `kid`, with concurrent
  misses collapsed into one fetch. This is a constraint
  rather than a deliverable — it is done, and no phase of
  this plan may regress it or duplicate it.
* Audit events (`EVENT_TYPE_AUDIT`) cover human logins with
  at least: issuer, subject, the rule or claim that granted
  access, the namespaces granted, and the token id (`jti`)
  where the issuer supplies one.
* The code passes `pre-commit run --all-files`.
* Functional coverage in
  `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/`
  exercises an end-to-end human OIDC login against a
  containerised provider, alongside the existing
  `test_federation.py` exchange coverage.
* `docs/{developer,operator,user}_guide/authentication.md`,
  `docs/glossary.md` and
  `docs/developer_guide/api_reference/authentication.md`
  are updated to describe the human login path alongside
  the existing workload exchange, and when to reach for
  each. All five are current as of auth federation phase 4,
  so this is an extension of live documents rather than a
  rewrite.
* A cluster that never configures an issuer behaves exactly
  as it does today.

### Future work

* **`issuer_url` is not required to be https**, although
  `jwks_uri` is and says why
  (`shakenfist/external_api/auth.py:919`). Filed as
  [#4890](https://github.com/shakenfist/shakenfist/issues/4890)
  by phase 3 rather than fixed there: it is a breaking change
  to an administrative API for deployments that cannot be
  inspected from here, so whether it wants a deprecation window
  is a judgement about real deployments. Phase 3's discovery
  fetcher refuses a plaintext URL of its own accord, so nothing
  fetches one today.

* **`Field(max_length=...)` never reaches the generated DDL.**
  `PYTHON_TO_SQLALCHEMY` maps `str` to `sa.String(255)`
  unconditionally (`shakenfist/schema/sqlalchemy.py:285`), so
  `issuer_url`, `jwks_uri` and `audience` have each declared a
  1024 character limit since they were written and are stored as
  `VARCHAR(255)`. The same root cause as #4559, which was fixed
  for one field without touching the mapping, so the class
  remains. Phase 3 used the documented `SQLLongText()` escape
  hatch for the three endpoint URLs it added, because those
  genuinely can exceed 255, and left the three pre-existing
  columns alone because changing them needs a data migration.
  The first review round then made every validator on this
  surface derive its limit from `stored_max_length()`, which
  answers from the generated column rather than the
  declaration, so a value these columns cannot hold is now
  refused with a 422 instead of truncated. That closes the
  symptom on the issuer routes and leaves the class open: every
  other over-declared `str` field in the schema still accepts
  values its column will not hold. The issue body is written
  and ready to file; it was not filed from the session that
  wrote it.

* **A refresh cannot update a `jwks_uri` that discovery
  supplied.** An operator-configured value always wins over a
  discovered one, which is the right default -- silently
  repointing where a cluster fetches signing keys is worse than
  a stale endpoint -- but it is applied to the stored value
  rather than to the operator's intent, and nothing records
  which of the two a stored `jwks_uri` came from. So an issuer
  configured by discovery alone can never have that field
  refreshed, and an operator whose provider moves its key set
  has to issue a PUT omitting `jwks_uri` to re-adopt the
  published value. Phase 3 documented the workaround. The fix is
  provenance on the column, so that a refresh replaces a
  discovered value and preserves an operator's.

* **Per-resource RBAC.** Roles like "read-only on
  namespace X" or "may create instances but not
  networks". The gap is narrower than it was when this
  bullet was written: [scopes](/glossary/#scope) now
  provide a family-and-verb axis, so "may write instances
  but not networks" and "read-only on everything" are both
  expressible today as a scope list on a key. What is still
  missing is a *per-object* axis — "this instance but not
  that one" — a verb vocabulary finer than read / write /
  delete, and a way to name a bundle of scopes as a role
  rather than enumerating them on every key. The unit of
  identity stays the namespace.
* **Federated trust.** Mapping a single human across
  several IdPs (e.g. internal IdP + partner IdP for
  contractors) onto one logical SF identity. Speculative.
* **Inter-node OIDC.** Move inter-node auth onto OIDC
  too. Deferred because of the IdP-dependency concern
  noted in open question 8, and if it is ever revisited the
  issuer is a node-identity one — SPIFFE, or a cloud
  provider's instance identity document — rather than the
  corporate IdP humans log in through.
* **Web console.** A browser UI for SF would naturally
  use the same OIDC flow with auth-code + PKCE. Not in
  scope here, but the auth design should not preclude it.
* **Token introspection / online revocation.** RFC 7662
  introspection or an SF-side revocation list. Whether this
  matters at all is a consequence of open question 13 rather
  than an independent choice, and 13 chose exchange: what
  reaches the request path is a Shaken Fist access token
  bound to a namespace key's nonce, revocation is immediate
  and inherited, and there is nothing here to build. It
  returns only if a later plan reopens 13 for
  direct-bearer, when revocation would acquire a bounded
  delay equal to the IdP's token lifetime and introspection
  would be the escape hatch.

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
