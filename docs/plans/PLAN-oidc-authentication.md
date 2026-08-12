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

These are preliminary sketches. Each will be tightened
significantly when this plan moves out of stub status.

1. **Issuer trust model.** How many IdPs can a cluster
   trust at once? One feels limiting (you might want
   "internal IdP for staff, partner IdP for contractors").
   Many means SF carries a list of trusted issuers and
   JWKS URLs in config. Possible resolution: support a
   list, validate the token's `iss` against the list, and
   pick the matching JWKS for signature verification.

2. **Claim → namespace mapping.** The simplest design is a
   single claim (configurable name, e.g. `sf_namespaces`)
   that carries a list of namespace names. Alternatively,
   group names in the IdP can be mapped to namespaces via
   SF-side config (e.g. group `eng-platform` → namespaces
   `platform`, `platform-ci`). The first is cleaner but
   pushes the mapping problem entirely onto IdP admins;
   the second keeps the policy in SF but adds config
   surface. Need to pick one (or support both).

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

4. **Audience and multi-tenant clusters.** OIDC tokens
   are issued to an `aud` (audience). SF should validate
   that the token's audience matches the cluster's
   configured audience identifier so that a token minted
   for some other service is not accepted as an SF
   token. What is the right default audience name?
   Configurable per cluster.

5. **What about the `system` namespace?** Today `system`
   is the bootstrap superuser and is in every namespace's
   trust list. Under OIDC, "is this caller a cluster
   admin" should be driven by a claim (e.g. a group
   `sf-admin`), not by membership in a namespace named
   `system`. The `system` namespace stays as the
   bootstrap / system-key holder; the admin *role* is
   what becomes a claim. Need to decide how the existing
   `caller_is_admin` decorator changes.

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

10. **Token caching on the client.** Where does
    `sf-client` cache the OIDC refresh token and access
    token? `~/.shakenfist/oidc-cache` is the obvious
    answer, with file mode 0600. Need to define the
    cache format and invalidation rules.

11. **Migration of existing namespace keys.** The
    rename to "service account tokens" is mostly
    cosmetic — keys keep working. But the user-facing
    CLI command names (`sf-client namespace add-key`)
    and the JSON shape of `keys` in `namespace_attributes`
    may want to evolve. Need to decide whether the
    rename is a pure UX layer over the existing
    storage or an actual schema migration.

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
