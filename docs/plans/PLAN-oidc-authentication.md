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
development priorities. Key references inside the repo
include `shakenfist/external_api/auth.py` (the `/auth`
endpoint and namespace-ownership decorators),
`shakenfist/util/access_tokens.py` (the JWT issue / parse
helpers built on `flask_jwt_extended`),
`shakenfist/namespace.py` (the `Namespace` DBO and the
nonced-key + trust model), `shakenfist/schema/namespace_attributes.py`
(the `keys` and `trust` JSON columns), and
`docs/{developer,operator,user}_guide/authentication.md`
(the current authentication user surface).

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

Shaken Fist authenticates today with a custom namespace-key
scheme:

1. **Namespace-scoped keys.** Each namespace carries a
   `keys` JSON attribute (`namespace_attributes.keys`) that
   stores one or more bcrypt-hashed keys, each with a name
   and a nonce. Keys are created via
   `sf-client namespace add-key`.
2. **The `/auth` endpoint** in
   `shakenfist/external_api/auth.py` takes a `{namespace,
   key}` body, walks the namespace's keys, bcrypt-compares
   the supplied key against each stored hash, and on success
   issues a JWT via `flask_jwt_extended.create_access_token`.
3. **JWT identity is `<namespace>:<keyname>`** — see
   `shakenfist/util/access_tokens.py`. The token includes a
   `nonce` claim that is verified against the stored nonce
   on every request; rotating a key bumps the nonce, which
   invalidates outstanding tokens for that key.
4. **Trust between namespaces** is a list on
   `namespace_attributes.trust` and grants visibility from
   trusted namespaces into the trusting namespace. `system`
   is in every namespace's trust list and cannot be removed.
5. **Inter-node authentication** reuses the same scheme via
   short-lived `_service_key*` keys created per request, as
   documented in
   `docs/developer_guide/authentication.md`.

What this model gets right:

* Long-lived bearer credentials are great for automation —
  CI systems, Ansible, the SF Python client all just hold a
  key in `~/.shakenfist` and call `/auth` when needed.
* Namespace ownership is unambiguous: the key *is* a
  capability on that namespace.
* JWT format and `Authorization: Bearer ...` semantics are
  already in place, so the wire shape will not change much.

What it does not give us:

* **No human SSO story.** A human operator cannot "log in
  with their corporate identity" — they have to be issued a
  shared static key and put it in a file.
* **No central account lifecycle.** Disabling a user means
  rotating keys across every namespace they had access to;
  there is no notion of "this human" independent of "this
  namespace key".
* **No group-driven namespace access.** A new namespace
  needs an explicit key minted for every entity that should
  reach it; there is no "engineering group has access to
  these N namespaces" primitive.
* **The keys are entirely Shaken Fist's problem.** Storage,
  hashing, rotation, nonce bookkeeping, and audit are all
  SF code. Outsourcing to a real IdP (Keycloak, Authentik,
  Okta, Entra ID, Google Workspace, ...) is currently
  impossible.

External design discussion summarised:

* **Both Authentik and Keycloak** can act as the OIDC
  provider. Both support custom claims, group → claim
  mapping, machine-to-machine via `client_credentials`, and
  long-lived service-account tokens. Either is acceptable
  upstream; SF should validate JWTs against whatever IdP
  the operator runs, not bind to a specific implementation.
* **Mapping SSO users to namespaces.** Standard pattern is
  a group claim in the JWT (e.g. `groups` or a
  custom-named claim) that lists the namespaces the bearer
  is permitted to act on. SF then authorises per-request
  against that claim. Most humans will be members of
  several namespaces, so we hand the full list in the
  token rather than asking the user to pick one at
  exchange time.
* **Existing namespace keys are not going away.** Machine
  credentials (CI, Ansible, the agent inside SF VMs) are a
  genuine need; GitHub and GitLab keep PATs alongside SSO
  for the same reason. The right outcome is for the current
  key model to be renamed and re-scoped as "service account
  tokens" — kept for non-human callers, with humans pushed
  through OIDC.
* **Outsourcing token issuance does not outsource
  authorisation.** Even with IdP-issued machine tokens,
  Shaken Fist still has to validate the JWT (via JWKS) and
  still has to map claims onto namespaces. We can shed
  token *storage and issuance* by leaning on the IdP, but
  not authorisation policy — that is irreducibly SF's job.

## Mission and problem statement

Add OIDC as an authentication option for Shaken Fist so
that:

* **Humans** can authenticate to the SF REST API using
  their corporate identity, via an OIDC flow appropriate to
  the client (auth code + PKCE for browser-driven clients,
  device code for the CLI on headless boxes, etc.). The
  resulting JWT is what `sf-client` and other clients carry
  in `Authorization: Bearer ...` exactly as today.
* **Namespace access for humans** is driven by claims in
  the OIDC-issued JWT, typically derived from group
  membership in the IdP. A user gains and loses access by
  being added to or removed from groups in the IdP, with
  no SF-side per-user bookkeeping.
* **Machines** continue to use long-lived bearer credentials
  for automation. The existing namespace-key mechanism is
  renamed and re-scoped to "service account tokens" but
  remains supported and is the default for non-human
  callers. Operators who prefer to outsource even machine
  tokens to their IdP (e.g. Keycloak service accounts via
  `client_credentials`) can do so, and SF treats those
  tokens identically to its own.
* **Authorisation lives in one place** in the SF code,
  keyed on the namespace claim(s) in the token, regardless
  of which issuer minted the token.
* **Existing deployments keep working.** OIDC is opt-in,
  configured per cluster. Clusters that never enable it
  behave exactly as before. Clusters that enable it gain a
  second issuer alongside the built-in one and both kinds
  of token are accepted in parallel during the transition.

Scope boundaries (preliminary — to be refined when this
plan moves out of stub status):

* **In scope:** OIDC discovery + JWKS-backed JWT
  validation; the SF-side claim → namespace mapping; the
  rename / re-framing of existing namespace keys as
  service-account tokens; the CLI flows needed for humans
  to obtain an OIDC token from `sf-client`; documentation
  of how to configure Keycloak and Authentik against SF.
* **Out of scope:** running an IdP inside SF. SF is the
  *relying party*; operators bring their own IdP.
* **Out of scope:** SAML, LDAP-direct, or other non-OIDC
  identity protocols. OIDC is the lingua franca and is the
  one we will support.
* **Out of scope (initially):** per-resource (not
  per-namespace) RBAC. The unit of authorisation is still
  the namespace. Finer-grained roles are deferred to
  future work.
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
