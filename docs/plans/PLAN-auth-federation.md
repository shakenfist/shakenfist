# Workload identity federation and first-class namespace keys

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
relevant source files, understand existing patterns (object
lifecycle, state machines, MariaDB storage via the three-layer
direct/gRPC/public pattern, Pydantic schemas, daemon
architecture, operation queue system, event logging), and
ground your answers in what the code actually does today. Do
not speculate about the codebase when you could read it
instead. Where a question touches on external concepts (OIDC
workload identity, JWT validation, JWKS rotation, GitHub
Actions OIDC claims, Authentik/Keycloak `client_credentials`
service accounts), research as needed to give a confident
answer. Flag any uncertainty explicitly rather than guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview, object types, and daemon structure. Consult
`CLAUDE.md` for build commands, project conventions, and
database access patterns. Consult `GOALS.md` for current
development priorities. Key references inside the repo for
this plan:

* `shakenfist/external_api/auth.py` — the `/auth` endpoint
  and namespace/key CRUD endpoints.
* `shakenfist/external_api/base.py` — `verify_token`,
  `caller_is_admin`, and the nonce re-verification against
  the minting key.
* `shakenfist/util/access_tokens.py` — JWT mint/parse
  helpers on `flask_jwt_extended`; identity is
  `<namespace uuid>:<keyname>`.
* `shakenfist/namespace.py` — the `Namespace` DBO,
  `add_key`/`remove_key`, the read-time expiry filter on
  `keys`, and the trust model.
* `shakenfist/schema/namespace_attributes.py` — the `keys`
  (nonced dict) and `trust` JSON columns.
* `shakenfist/daemons/cleaner/` — the housekeeping daemon
  that will gain key reaping.
* `docs/{developer,operator,user}_guide/authentication.md`
  — the current authentication documentation surface.
* `docs/plans/PLAN-oidc-authentication.md` — the sibling
  (stub) plan for *human* OIDC login. This plan is the
  machine/workload half; see "Relationship to the OIDC
  authentication plan" below.

When we get to detailed planning, the convention is a
separate plan file per detailed phase, named
`PLAN-auth-federation-phase-NN-descriptive.md` in the same
directory, tracked in the Execution table below.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Shaken Fist authenticates callers with namespace-scoped keys:
bcrypt-hashed entries in the `nonced_keys` dict of the
`namespace_attributes.keys` JSON column. `/auth` walks the
namespace's keys, bcrypt-compares the presented secret, and
mints a JWT whose identity is `<namespace uuid>:<keyname>`
and which carries the key's `nonce` as a claim
(`util/access_tokens.py`). On every request, `verify_token`
re-looks-up the minting key and rejects tokens whose nonce no
longer matches (`external_api/base.py`), so deleting or
rotating a key immediately invalidates all outstanding
tokens minted from it.

Facts about the current implementation that shape this plan:

* **Key expiry half-exists.** `Namespace.add_key()` accepts
  an optional `expiry`, and the `keys` accessor filters
  expired entries at read time (`namespace.py`). Because
  both `/auth` and `verify_token` read through that
  accessor, an expired key can neither mint new tokens nor
  validate outstanding ones — expiry is enforced exactly, at
  use time. But expired entries are only *hidden*, never
  deleted from storage, and expiry is not surfaced through
  the API or `sf-client`.
* **Keys are not objects.** They are anonymous dict entries:
  no per-key events, no soft-delete lifecycle, no attributes
  beyond hash/nonce/expiry, no place to hang scopes or
  provenance.
* **Tokens are all-powerful within their namespace.** There
  is no notion of a token (or key) that may only touch, say,
  blobs and artifacts.
* **Minted JWTs are logged into the event stream.**
  `create_token()` writes the entire token into a namespace
  audit event (`util/access_tokens.py`). Events are
  namespace-scoped, but this pattern must not be repeated
  for federated key material, and is worth revisiting.

The motivating use case is CI caching: ephemeral GitHub
Actions runners (created by a CI conductor that cannot know
at provision time which repository's job will land on a
runner) need scoped, short-lived access to per-repository
cache namespaces on a Shaken Fist cluster. GitHub Actions
mints an OIDC identity token per job whose claims
(`repository`, `ref`, `event_name`, `job_workflow_ref`)
cryptographically identify the workload — but only at job
runtime, on the runner itself. The clean design is therefore
an *exchange*: the workflow presents the GitHub-signed JWT to
Shaken Fist, which validates it against a trusted-issuer
configuration and mints a **namespace key** with a defined
expiry and a defined set of permitted operations. The caller
then uses that key exactly as any `sf-client` user does
today, including automatic token re-mint mid-job. The nonce
mechanism gives revocation of derived tokens for free when
the key expires or is deleted.

Nothing in the exchange design is GitHub-specific: the same
trusted-issuer + claim-mapping machinery must accommodate a
future Authentik/Keycloak issuer (e.g. `client_credentials`
service accounts) with only configuration.

### Relationship to the OIDC authentication plan

`PLAN-oidc-authentication.md` (stub) covers *humans* logging
in with corporate identity, where IdP-issued JWTs are used
directly as bearer tokens and namespace access is derived
from group claims. This plan covers *workloads* exchanging an
IdP-issued JWT for a scoped namespace key. They share
infrastructure this plan builds first: trusted-issuer
configuration, JWKS fetch/cache/rotation, and JWT signature +
claim validation. They differ after validation: this plan
mints a key; the human plan authorises requests directly off
the external token. Phase 2 here (keys as first-class
objects) is also the groundwork for that plan's
"service-account token" re-framing of namespace keys (its
open question 11). Decisions here should be taken with that
plan on the desk; phase 5 of this plan exists to rewrite
that stub against whatever phases 1–4 actually build.

### Design principles (from the design discussion, 2026-07-14)

1. **Attribute-based issuance, scope-based
   enforcement.** All policy intelligence — evaluating the
   external token's claims against a mapping rule — runs
   once, at the exchange endpoint. What comes out is a key
   (and, derived from it, tokens) carrying a dumb, explicit
   list of permitted operations. Per-request enforcement is
   set membership against an endpoint tag, not attribute
   evaluation. No policy engine in the hot path.
2. **The exchange yields a key, not a token.** A
   `(namespace, key)` pair is the credential shape every
   existing consumer understands, including the client's
   automatic re-auth when a token expires mid-job; and the
   existing nonce mechanism means key expiry/deletion
   revokes all derived tokens immediately.
3. **Issuer-generic by construction.** Trusted issuers and
   mapping rules are data, not code. GitHub Actions is the
   first issuer; an Authentik or Keycloak issuer must be
   addable without a code change.
4. **Fail closed for scoped credentials.** Tokens minted
   from a scoped key are default-deny on any endpoint not
   yet tagged with a required operation. Tokens minted from
   traditional (unscoped) keys carry an implicit wildcard,
   so existing deployments are unaffected.
5. **Never log secret material.** The exchange logs key
   *name*, scopes, expiry, and the inbound claims that
   satisfied the rule — never the key itself. The existing
   token-in-event behaviour of `create_token()` is
   revisited in phase 2.
6. **Check-at-use is the enforcement; the cleaner is
   hygiene.** Expiry is already enforced exactly at use
   time via the filtered accessor. The cleaner daemon's new
   reaping loop exists to garbage-collect dead entries and
   emit lifecycle events, and nothing about security may
   depend on its cadence.

## Mission and problem statement

Give Shaken Fist a first-class, auditable model for
credential issuance and scoping, so that an external
workload identity (initially a GitHub Actions job) can be
exchanged for a time-bounded, operation-scoped namespace
key without any party having to hold a long-lived secret on
the workload's behalf. Along the way, promote namespace keys
from anonymous dict entries to first-class objects, pin down
the project's authentication vocabulary, and document the
result for operators and users.

Explicitly deferred: the CI conductor's adoption of the
exchange (provisioning cache namespaces, the save/restore
actions in `shakenfist/actions`, ref-scoped cache-poisoning
rules). That work follows in its own plan once this
groundwork exists, and lives mostly outside this repository.

## Open questions

1. **Scope vocabulary.** Three candidate shapes were
   discussed:
   * *Hand-defined intent verbs* — coarse
     `resource-family.verb` strings (`blob.read`,
     `artifact.write`). Readable policy language, but every
     endpoint must be hand-tagged, which creates the
     coverage long-tail in open question 2.
   * *Object name + REST verb* (`instance.get`,
     `artifact.post`) — mechanically derivable from the
     resource class and HTTP method, so coverage is
     automatically complete. But HTTP verbs are
     implementation vocabulary, not policy vocabulary
     (operators should not need to know whether an upload
     is POST or PUT to reason about a rule); POSTed
     sub-resource actions conflate (`instance.post` is both
     "create" and "reboot"); and capability strings become
     coupled to routing, so a REST refactor (e.g. the
     artifact UX rework) silently churns or widens
     long-lived mapping rules.
   * *Hybrid (current lean)* — intent verbs, mechanically
     derived: GET/HEAD → `.read`, POST/PUT/PATCH →
     `.write`, DELETE → `.delete`, with an explicit
     per-endpoint override where the derivation misleads
     (e.g. sub-resource power actions stay
     `instance.write`, or gain a named `instance.power` if
     they ever need separating). Keeps automatic coverage,
     a three-verb operator vocabulary, and insulation from
     route changes.
   Implementation sketch for the hybrid: flask-restful
   resource methods are literally named after the HTTP
   verb, so the verb derives from the method name and the
   object family from the resource class (a class
   attribute where the class name is unhelpful).
   Enforcement itself lives on the already-universal
   `verify_token` path, so derivation-based checking
   applies to every authenticated endpoint without anyone
   remembering to decorate; a lightweight decorator taking
   keyword arguments with these derived defaults (e.g.
   `@scope(verb='power')`) exists purely to *annotate*
   overrides at the decoration site, where they are
   greppable and visible in review. The override audit in
   open question 2 is then one grep.
   Phase 3 must publish the chosen vocabulary, the
   derivation rule, and the rule for growing it. If the
   hybrid is chosen, open question 2 largely dissolves.
   Decided (2026-07-15, forced by the phase 1 terminology
   survey): the noun is **scope**, not "capability" —
   `check_capability` already names the client's
   feature-probe mechanism, and reusing the word would put
   two meanings in the same CLI surface. The vocabulary
   *shape* (hybrid derivation) remains open.
2. **Endpoint tagging coverage.** Phase 3 tags at minimum
   the blob and artifact endpoints (the CI cache needs).
   Untagged endpoints are default-deny for scoped tokens.
   Do we accept a long tail of untagged endpoints, or drive
   to full coverage within the phase? Note this question
   only exists in its hard form under hand-tagging; the
   hybrid derivation in open question 1 makes coverage
   automatic, reducing this to auditing the override list.
3. **Ownership model for mapping rules.** Current lean
   (from design discussion): split the concept in two.
   *Trusted issuers* (issuer URL, JWKS, audience) are
   cluster-level, system-owned objects — "who may vouch
   for identities here" is an admin decision. *Mapping
   rules* (bound claims → scopes, TTL, key template) are
   owned by the namespace they target, like instances and
   networks, because a rule is a standing, claim-gated
   authorization to mint keys in that namespace — the same
   privilege class as `add-key`, gated the same way
   (namespace ownership, or admin). Rules reference their
   issuer; minted keys reference their rule in provenance;
   so the full chain issuer ← rule ← key ← token is
   object-modelled. Consequences: rules are deleted with
   their namespace; "who can get into this namespace" is
   answered by listing its rules (the inbound sibling of
   the trust list); the exchange request names its target
   (`{identity token, namespace, rule name}`), so matching
   is one lookup plus one claim check with no
   cross-namespace rule enumeration; a workflow needing
   two namespaces exchanges its token twice against two
   rules. Deliberately given up: templated namespace
   auto-creation (`gh-{repository-name}`) — there is no
   namespace yet to own such a rule, and pre-creating
   namespace + rule per repository belongs to the
   orchestration layer (the CI conductor) rather than the
   platform. To resolve in the phase 3 plan: whether
   multiple rules per namespace may bind the same issuer,
   and what rule mutation means for keys already minted
   from it (lean: nothing — keys stand alone once minted,
   with provenance recording the rule as it was).
4. **Exchange endpoint abuse resistance.** The exchange is
   necessarily reachable without an SF credential (its
   authentication *is* the external JWT). It must be cheap
   to reject garbage: issuer allowlist check before JWKS
   fetch, JWKS cached with sane TTL and single-flight
   refetch on unknown `kid`, per-source rate limiting, and
   strict maximum token size. How much of this is v1?
5. **Key visibility and naming.** With phase 2, keys are
   first-class objects owned by their namespace, so
   provenance, expiry, and scopes are queryable attributes
   — a federated key is distinguished by its rule
   reference, not by smuggling metadata into its name, and
   "show me every key rule X minted" is an ordinary
   filtered listing. What actually remains open:
   * Collision handling for rule-minted names: the rule's
     key-name template (e.g. incorporating the workflow
     run id) can collide on re-runs of the same run — does
     the exchange refuse, replace, or suffix?
   * How much the legacy `key_names` API shape exposes:
     it must keep returning names for existing clients,
     but does it include federated keys (lean: yes — they
     are real keys, and hiding them from the legacy view
     makes audits lie), with richer detail reserved for
     the new object listing?
   * Whether a light naming convention is still worth
     having purely for human scanning of mixed listings
     (lean: let the rule's template decide; no enforced
     prefix).
6. **JWT lifetime vs key lifetime.** The nonce check
   already invalidates derived tokens the moment the key
   expires, so capping `expires_delta` at the key's
   remaining lifetime is cosmetic. Do it anyway for
   clarity, or leave mint-time duration alone?
7. **Migration mechanics for key storage.** The decision
   to make keys first-class namespace-owned objects (with
   rule references, provenance, per-key events, cleaner
   reaping, and filtered listings) effectively forecloses
   wrapping object semantics around the existing
   `namespace_attributes.keys` JSON column: real
   relationships and SQL-level filtering want a real table
   with a Pydantic schema, per the codebase's standard
   object shape and the BYO-MariaDB direction. What
   remains open is the transition:
   * Migration path for existing `nonced_keys` entries
     (bcrypt hashes and nonces copy verbatim; no expiry,
     wildcard scope): one-shot migration at upgrade, or a
     dual-read window where `/auth` and `verify_token`
     consult the table first and fall back to the column?
   * Rollback story if the migration must be reversed
     after new-style keys (with expiry/scopes) exist.
   * When the legacy column is retired: immediately after
     migration, or kept read-only for a deprecation
     window?
   * Hot-path cost: `verify_token` re-verifies the nonce
     on every request, so the key lookup moves from an
     attribute-blob read to an indexed table read —
     confirm this is neutral-or-better, and decide whether
     any caching is warranted (with care: a stale cache
     would delay nonce-based revocation, which is the
     mechanism's whole point).
8. **Glossary location.** Resolved by phase 1 (2026-07-15):
   a single `docs/glossary.md` at the top level, in the
   mkdocs navigation after Features, linked from the three
   authentication guides and `objects.md`.
9. **`system` interplay.** Scoped keys in the `system`
   namespace would today pass `caller_is_admin` (it only
   checks the namespace name). Phase 3 must decide whether
   admin endpoints also require a scope (e.g.
   `admin.*`) so a scoped system-namespace key cannot
   escalate. Related to the sibling plan's open question 5.
10. **Opt-out rather than opt-in enforcement.** The
    "must remember to decorate" problem predates this plan:
    `verify_token` itself is applied by hand per method,
    so a forgotten decorator is a silently open endpoint.
    Inverting this flips the failure mode from fail-open to
    fail-closed: apply authentication and derived
    scope enforcement universally (either via
    `method_decorators` on the shared `api_base.Resource`
    base — class-level decorators run outermost, so auth
    correctly precedes the per-method ownership checks — or
    via an app-wide `before_request` hook), with a small
    explicit `@public` annotation for the genuinely
    unauthenticated endpoints (`/auth` POST, the federated
    exchange, the health probes already special-cased in
    `HEALTH_PROBE_PATHS`). The audit then inverts from
    "did every endpoint remember auth?" to "is every
    `@public` justified?", and a custom pre-commit check
    (precedent: the `from_db_by_ref` scoping hook) can
    backstop the pattern. Semantic decorators
    (`caller_is_admin`, `requires_namespace_ownership`)
    remain opt-in — they are per-endpoint policy, not
    defaults. Phase 3 should decide whether this inversion
    is in scope or a fast-follow refactor.
11. **Scopes must compose with trust.** Namespace trust
    grants cross-namespace visibility, and the deferred CI
    conductor design leans on it (a PR-scratch namespace
    with read-trust on the per-repo cache namespace). A
    scoped key's scopes must follow it across the
    trust boundary — `blob.read` means "may read blobs it
    can see", wherever trust makes them visible, and a
    scoped token must never gain wildcard behaviour just
    because the object it touches lives in a trusting
    namespace. Phase 3 needs a test asserting exactly
    this, or trust becomes a scope-escape hatch.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Terminology and glossary | [PLAN-auth-federation-phase-01-glossary.md](PLAN-auth-federation-phase-01-glossary.md) | Complete |
| 2. Namespace keys as first-class objects | PLAN-auth-federation-phase-02-key-objects.md | Not started |
| 3. Federated exchange and scope enforcement | PLAN-auth-federation-phase-03-exchange.md | Not started |
| 4. Authentication documentation | PLAN-auth-federation-phase-04-docs.md | Not started |
| 5. OIDC plan refresh | PLAN-auth-federation-phase-05-oidc-plan-refresh.md | Not started |

Phase plans for phases 2–5 have not been drafted yet; the
open questions above should be resolved (or explicitly
carried into the relevant phase plan) before each phase is
cut.

### Phase 1: Terminology and glossary

Nail down the vocabulary this plan (and the sibling OIDC
plan) needs, and fold in other overloaded terms the codebase
already uses. Deliverable: a glossary page in `docs/`,
linked from the three authentication guides and registered
in the docs navigation.

Authentication terms to pin (from the design discussion):

* **identity token** — an externally-issued JWT proving
  workload or user identity (e.g. GitHub Actions OIDC
  token, Authentik-issued token).
* **trusted issuer** — an external token issuer the cluster
  is configured to accept, with its JWKS location and
  expected audience.
* **mapping rule** — a first-class object, owned by the
  namespace it targets, that is a standing claim-gated
  authorization to mint keys there: a trusted-issuer
  reference, bound claims, scopes, expiry.
* **namespace key** — the stored credential (bcrypt hash +
  nonce, now optionally expiry, scopes, provenance) from
  which access tokens are minted.
* **access token** — a Shaken Fist-issued JWT, minted from
  a namespace key via `/auth`, nonce-bound to that key.
* **scope** — a `resource-family.verb` string naming an
  operation class a key (and its tokens) may perform. Not
  "capability": that word already names the client's
  server-feature-probe mechanism (`check_capability`).
* **nonce** — the per-key value embedded in derived tokens
  and re-verified on every request; the revocation
  mechanism.
* **trust** — the existing namespace-to-namespace
  visibility grant (unchanged by this plan, but must be
  defined to stop it being confused with issuer trust).

Candidate non-auth terms to sweep for and define in the same
pass (the artifact/blob/label cluster is already the subject
of `PLAN-artifact-ux-rework.md` and should be defined
consistently with it): artifact, blob, label, upload,
snapshot, namespace, instance, node roles, agent operation,
side channel, DBO/state machine states. The phase plan
should include a deliberate sweep for others rather than
assuming this list is complete.

### Phase 2: Namespace keys as first-class objects

Promote keys from `nonced_keys` dict entries to
`DatabaseBackedObject`s with the standard lifecycle:

* Attributes: key name, bcrypt hash, nonce, optional
  expiry, scopes (default wildcard), provenance (a mapping
  rule reference plus the satisfied claims, for
  exchange-minted keys; empty for operator-created ones),
  owning namespace.
* Per-key audit events (created, used-for-mint (sampled or
  rate-limited if noisy), expired, soft-deleted).
* Soft delete via the standard state machine; the cleaner
  daemon gains a loop that soft-deletes expired keys and
  hard-deletes long-soft-deleted ones. Enforcement remains
  the read-time filter — the cleaner is hygiene only.
* Expiry surfaced through the API and
  `sf-client namespace add-key --expiry ...`; key listings
  gain expiry/scope/provenance columns.
* Preserve exact `/auth` and `verify_token` semantics,
  including the nonce mechanism, and the `key_names` API
  shape for existing clients.
* Keys move to their own table with a Pydantic schema (the
  standard object shape; enables the rule/provenance
  references and SQL-level filtered listings). Existing
  `nonced_keys` entries migrate with no expiry and
  wildcard scope; transition mechanics per open question
  7.
* Stop writing minted JWTs into audit events; log token
  metadata (keyname, expiry, jti if we add one) instead.

### Phase 3: Federated exchange and scope enforcement

* **Trusted issuer objects** (admin-managed, system
  namespace only): issuer URL, JWKS endpoint/caching,
  audience. "Who may vouch for identities on this cluster"
  is a cluster-level decision.
* **Mapping rule objects**, owned by the namespace they
  target (creation gated like `add-key`: namespace
  ownership or admin): a reference to a trusted issuer,
  bound claims (e.g. `repository_owner`, `repository`,
  `ref`), scopes, key TTL, key-name template. A rule is a
  standing, claim-gated authorization to mint keys in its
  owning namespace; see open question 3 for the ownership
  rationale. The phase plan must define claim-matching
  semantics precisely: exact values and enumerated
  alternatives first, anchored patterns only with explicit
  justification — permissive pattern-matching on bound
  claims is the classic OIDC-federation vulnerability, and
  a sloppy pattern silently widens a rule. CRUD APIs plus
  `sf-client federation ...` commands.
* **Exchange endpoint** (e.g. `POST /auth/federated`):
  request names its target — `{identity token, namespace,
  rule name}`. Validates the presented identity token
  (signature via cached JWKS, `iss` matching the rule's
  issuer, `aud`, `exp`), checks the rule's bound claims,
  mints a scoped expiring key in the owning namespace, and
  returns `(namespace, key name, key)`. The key's
  provenance records the rule and the satisfied claims.
  Successful exchanges write an audit event carrying the
  satisfied claims — never the secret. Failed exchanges
  are audited too, against the rule's owning namespace: a
  stream of near-miss claim failures is what probing looks
  like, and the namespace owner is the party who needs to
  see it.
* **Scope enforcement**: scopes copied from key into
  token claims at mint; enforcement lives on the universal
  `verify_token` path with scopes derived per the open
  question 1 hybrid, and a lightweight annotation
  decorator for per-endpoint overrides; wildcard for
  tokens minted from unscoped legacy keys; default-deny
  for scoped tokens wherever derivation is impossible.
  The open-question-9 decision about admin endpoints and
  the open-question-10 decision about opt-out inversion
  land here.
* **Abuse resistance** per open question 4, including
  replay: the exchange should be single-use per inbound
  token `jti` *per rule* — repeat exchange of the same
  token against the same rule is refused, while the
  legitimate "one token, two rules, two namespaces"
  pattern still works.
* GitHub Actions is the worked first issuer; the phase
  plan must demonstrate (at design level) an Authentik
  `client_credentials` rule differing only in
  configuration.

### Phase 4: Authentication documentation

Update `docs/{developer,operator,user}_guide/authentication.md`
and cross-link the glossary:

* Developer guide: key objects, nonce revocation, scope
  enforcement, the exchange flow, how issuance and
  enforcement split.
* Operator guide: configuring trusted issuers and mapping
  rules, worked GitHub Actions example (a generic
  "grant a repository's workflows scoped access to a
  namespace" recipe — written against public GitHub
  Actions concepts only, not the private CI conductor's
  internals), key lifecycle and reaping.
* User guide: what a federated key is, how expiry and
  scopes surface in `sf-client`.

The GitHub example must stand alone for any reader running
their own runners; nothing in `docs/` should describe or
depend on the private CI conductor implementation.

### Phase 5: OIDC plan refresh

Rewrite `PLAN-oidc-authentication.md` (the human-login
sibling, currently a stub) against the as-built reality of
phases 1–4, so it plans forward from what exists rather
than from the pre-federation codebase:

* Its Situation section describes key objects, scopes, the
  trusted-issuer configuration, and the exchange endpoint
  as existing infrastructure, with pointers to the
  glossary's terms.
* Its tentative phases 1–2 (JWT validation refactor; OIDC
  validator with discovery/JWKS) are marked superseded by
  this plan's phase 3, and its remaining phases renumbered
  around what is genuinely left: interactive CLI flows,
  claim-driven multi-namespace authorisation, admin-as-a-
  claim, IdP worked examples, and functional testing.
* Its open question 1 (issuer trust model) is recorded as
  resolved by the trusted-issuer objects; open question 11
  (service-account rename: UX or schema migration) is
  re-answered in terms of key objects.
* A new open question is added: whether human login should
  use IdP tokens directly as bearer credentials
  per-request (its original design) or exchange them for a
  short-lived scoped session credential via the phase 3
  machinery, with the trade-offs (multi-namespace access
  favours direct-bearer; revocation and a single
  enforcement path favour exchange) laid out for its own
  phase 0 decisions pass.
* Anything phases 1–4 shipped that contradicts other text
  in the stub is corrected, so the two plans never
  disagree about the codebase.

This phase is documentation-only and closes the loop the
"Relationship to the OIDC authentication plan" section
opens: constraints discovered while building the machine
half are recorded in the human half's plan, not left in
commit messages and heads.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making. The workflow, effort
levels, model choice guidance, and review checklist follow
`PLAN-TEMPLATE.md` exactly; each phase plan carries its own
step-level table (Step / Effort / Model / Isolation / Brief).

### Planning effort

* Phase 1 (glossary): **medium** — mostly survey and
  writing; the auth terms are already settled above.
* Phase 2 (key objects): **high** — storage migration,
  lifecycle semantics, and strict behaviour-preservation of
  `/auth` and `verify_token` need careful design and strong
  test coverage before/after.
* Phase 3 (exchange): **high** — security-sensitive
  surface; JWKS handling, claim binding, and fail-closed
  enforcement all have sharp edges. Research GitHub's OIDC
  claim set and Authentik/Keycloak token shapes during
  planning, not implementation.
* Phase 4 (docs): **medium**, but review at high effort —
  the "don't reveal the conductor" constraint is a
  judgement call on every page.
* Phase 5 (OIDC plan refresh): **medium** — documentation
  only, but it requires accurately summarising what phases
  1–4 shipped and framing an architectural trade-off
  (direct-bearer vs exchange-based sessions) fairly for a
  decision that is deliberately not being made yet.

### Management session review checklist

As per `PLAN-TEMPLATE.md`, plus for this plan specifically:

- [ ] No secret material (keys, tokens) is written to
      events, logs, or fixtures anywhere in the diff.
- [ ] Scoped-token behaviour is fail-closed on untagged
      endpoints, proven by a unit test.
- [ ] Scopes compose with namespace trust — a scoped token
      touching objects visible via trust keeps its scopes
      (open question 11), proven by a unit test.
- [ ] Legacy key/token behaviour is bit-compatible, proven
      by tests that pre-date the change.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully
implemented because the following statements will be true:

* A GitHub Actions workflow, holding nothing but its own
  OIDC token, can exchange it against a configured mapping
  rule for a namespace key that expires, is scoped to blob
  and artifact operations, and works with an unmodified
  `sf-client`.
* Deleting or expiring that key immediately invalidates
  tokens minted from it (existing nonce semantics, proven
  by test).
* An equivalent mapping rule for an Authentik-style issuer
  requires configuration only — no code change.
* Namespace keys are database-backed objects with events,
  soft delete, expiry, scopes, and provenance; existing
  keys and clients are unaffected; expired keys are reaped
  by the cleaner daemon.
* Scoped tokens are default-deny on untagged endpoints;
  unscoped (legacy) tokens behave exactly as before.
* Minted secrets no longer appear in audit events.
* A glossary exists in `docs/`, is linked from the three
  authentication guides, and this plan's terms are used
  consistently across code, CLI help, and docs.
* The code passes `pre-commit run --all-files` (flake8,
  stestr unit tests, mypy); new code follows the
  three-layer database pattern and Pydantic schema
  conventions; functional coverage exercises the exchange
  end-to-end in `shakenfist/deploy/cluster_ci`.
* `docs/{developer,operator,user}_guide/authentication.md`
  are updated, and describe the feature without reference
  to the private CI conductor.
* `ARCHITECTURE.md`, `README.md`, and `AGENTS.md` are
  updated for the new object types and endpoints.
* `PLAN-oidc-authentication.md` has been rewritten against
  the as-built infrastructure: superseded phases marked,
  resolved open questions recorded, and the direct-bearer
  versus exchange-based-session question posed for its own
  phase 0 — the two plans nowhere disagree about the
  codebase.

### Future work

* **CI conductor integration** (its own plan, in the
  conductor's repository): pre-create per-repo cache
  namespaces and mapping rules; ref-scoped scratch
  namespaces with read-trust on the per-repo namespace to
  enforce the actions/cache poisoning rule (PR-ref writes
  never readable by trusted builds); retention/pruning of
  cache artifacts.
* **Cache save/restore actions** in `shakenfist/actions`:
  composite actions that request the GitHub OIDC token,
  exchange it, and tar/untar paths via `sf-client` blob
  operations.
* **Human OIDC login** — `PLAN-oidc-authentication.md`
  proceeds on top of this plan's trusted-issuer and JWT
  validation infrastructure.
* **Publishing the CI conductor** (currently the private
  `private-ci` repository, hypothetically as
  `shakenfist/ci-conductor`): deliberately *not* a phase of
  this plan. Beyond the missing deployment story and
  authentication the operator already noted, the working
  tree and git history contain embedded secrets (at
  minimum, a shared CI SSH private key inside
  `conductor/templates/userdata.yaml.j2`), so publication
  requires credential rotation plus either a history scrub
  or a fresh-start repository, and its own security review.
  This plan reduces what the conductor must keep secret
  (fewer long-lived credentials), which makes eventual
  publication easier; revisit once the conductor has grown
  a deployment story.
* **Token introspection / jti denylist** if bounded-delay
  revocation of *scoped keys themselves* (as opposed to
  their derived tokens) ever proves insufficient.
* **Templated mapping rules** with namespace auto-creation,
  per open question 3, if per-repo rule sprawl becomes
  real.

### Bugs fixed during this work

(none yet — but note the pre-existing behaviour of
`create_token()` logging whole JWTs into audit events, which
phase 2 removes, and the silent accumulation of expired
`nonced_keys` entries, which the cleaner loop fixes.)

### Documentation index maintenance

When this plan changes status:

* `docs/plans/index.md` — rows for this plan's phases live
  in the Plan Status table; keep them current.
* `docs/plans/order.yml` — this master plan is registered;
  phase files are not.

### Back brief

Before executing any step of this plan, the implementing
sub-agent must back brief the operator as to its
understanding of the phase plan and how the work it intends
to do aligns with that plan.
