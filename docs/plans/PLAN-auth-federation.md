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
  plan for *human* OIDC login, rewritten by phase 5 against
  the as-built infrastructure. This plan is the
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

`PLAN-oidc-authentication.md` covers *humans* logging in
with corporate identity, with namespace access derived from
group claims. This plan covers *workloads* exchanging an
IdP-issued JWT for a scoped namespace key. They share
infrastructure this plan builds first: trusted-issuer
configuration, JWKS fetch/cache/rotation, and JWT signature +
claim validation.

Where they may diverge is what happens after validation.
This plan mints a key. The human plan's *original* design
authorised requests directly off the external token, using
IdP-issued JWTs as bearer credentials — but that is no
longer a settled part of it. Phase 5 re-posed direct-bearer
versus exchange as that plan's own open question 13, to be
decided by its phase 0 rather than assumed here, so nothing
in this plan should be read as having already chosen for the
human half. Phase 2 here (keys as first-class objects) is
also the groundwork for that plan's "service-account token"
re-framing of namespace keys (its open question 11).
Decisions here should be taken with that plan on the desk;
phase 5 of this plan rewrote it against what phases 1–4
actually built.

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
   two meanings in the same CLI surface.

   **Resolved by phase 3 (2026-08-03).** The hybrid was
   chosen and shipped in step 3b. Verbs derive from the HTTP
   method (`read`/`write`/`delete`) and families from the
   resource class, with `scope_family` and
   `@api_base.scope(...)` as the greppable overrides. Two
   further verbs exist only as overrides, because there the
   HTTP method describes the mechanism rather than the
   privilege: `console` (the VDI helpers are GET, but they
   return interactive control of a guest) and `execute`
   (in-guest command execution is not the same privilege as
   creating an instance). Adding a verb is a vocabulary
   decision, and the test applied is whether anyone would
   sensibly write a mapping rule granting it alone. The
   vocabulary and derivation rule are published in
   `docs/developer_guide/authentication.md`, and the full
   family and verb sets are pinned by a test over the real
   routing table.
2. **Endpoint tagging coverage.** Phase 3 tags at minimum
   the blob and artifact endpoints (the CI cache needs).
   Untagged endpoints are default-deny for scoped tokens.
   Do we accept a long tail of untagged endpoints, or drive
   to full coverage within the phase? Note this question
   only exists in its hard form under hand-tagging; the
   hybrid derivation in open question 1 makes coverage
   automatic, reducing this to auditing the override list.

   **Resolved by phase 3 (2026-08-03).** Dissolved, as
   anticipated. Coverage is total by construction: every
   endpoint derives a scope from its class and method, and
   an endpoint whose scope *cannot* be derived is
   default-deny for scoped tokens rather than being quietly
   ungoverned. There is no long tail to accept. The override
   list is one grep and is published in the developer
   guide.
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

   **Resolved by phase 3 (2026-08-03).** The split shipped
   as described: issuers are system-owned and managed under
   `/auth/issuers`, rules are namespace-owned and managed
   under `/auth/namespaces/{namespace}/rules`, gated by
   `requires_namespace_ownership`, unique on
   `(namespace, name)`, and hard deleted with their
   namespace.

   Multiple rules per namespace **may** bind the same
   issuer. Uniqueness is on the rule's name, not on its
   issuer, and there is no reason to stop a namespace
   offering two different claim-gated grants to two
   different workloads from one provider.

   Rule mutation does **nothing** to keys already minted, as
   leaned. A minted key stands alone and its provenance
   records the claims that were actually satisfied, so the
   audit trail describes the grant as it was made rather
   than as the rule reads today. Narrowing a rule's scopes
   therefore does not retroactively narrow its keys; delete
   the keys if that is what is wanted. This is documented in
   the API reference rather than left to be discovered.

   One consequence was not anticipated and is worth naming:
   rules reference their issuer **by name**, so deleting and
   recreating an issuer under the same name silently rebinds
   every rule that named it. Storing the uuid would fail
   loudly instead. This was left as-is — the name is what an
   operator writes and reads — and is called out in the
   operator guide.
4. **Exchange endpoint abuse resistance.** The exchange is
   necessarily reachable without an SF credential (its
   authentication *is* the external JWT). It must be cheap
   to reject garbage: issuer allowlist check before JWKS
   fetch, JWKS cached with sane TTL and single-flight
   refetch on unknown `kid`, per-source rate limiting, and
   strict maximum token size. How much of this is v1?

   **Resolved by phase 3 (2026-08-03).** All of it is v1.
   The ordering is enforced and tested as a property rather
   than left to reading order: size
   (`FEDERATION_MAX_TOKEN_BYTES`, refused before parsing),
   then the issuer allowlist check against the unverified
   `iss` (no network yet), then the rate limit, and only
   then the JWKS fetch. JWKS caching uses `PyJWKClient` with
   a configured `lifespan`
   (`FEDERATION_JWKS_CACHE_SECONDS`) plus a per-issuer lock,
   so concurrent misses on a rotated key collapse into one
   fetch rather than a stampede against the provider.

   Two protections were added beyond the question's list.
   Replay is refused per `(token, rule)` via a composite
   primary key, so the failing insert *is* the detection.
   And both the replay claim and the rate limit counter fail
   **closed**: a database error answers 503 rather than
   being read as "not seen before" or "under the limit",
   because both of those readings authorise something.
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

   **Resolved by phase 3 (2026-08-03).** Collisions are
   avoided rather than arbitrated: `key_name_prefix` is a
   prefix, not a template, and the exchange appends a random
   discriminator. So a workflow re-run gets its own key
   rather than silently rotating the secret out from under a
   still-running job — which is what "replace" would have
   done, and is the failure mode the question was circling.
   Refusing was rejected for the same reason.

   The legacy `key_names` shape includes federated keys, as
   leaned: they are real keys, and hiding them would make
   audits lie. No naming convention is enforced beyond the
   operator's chosen prefix.
6. **JWT lifetime vs key lifetime.** The nonce check
   already invalidates derived tokens the moment the key
   expires, so capping `expires_delta` at the key's
   remaining lifetime is cosmetic. Do it anyway for
   clarity, or leave mint-time duration alone?

   **Resolved by phase 3 (2026-08-03).** Mint-time duration
   is left alone. The capping really is cosmetic — an
   expired key stops validating immediately, so a token
   outliving its key on paper cannot be used — and adding a
   second place where a lifetime is decided is a second
   place for the two to disagree. A federated key's own
   `key_ttl` is what bounds the grant, and that is the
   number an operator sets and reads.
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

   **Resolved by phase 2 (2026-07-27).** One-shot migration,
   no dual-read window. Schema migrations here are
   operator-driven via `sf-ctl ensure-mariadb-schema`, and
   `sf-database` refuses to start against a stale schema, so
   there is no window in which old and new code run against
   the same database and nothing for a dual read to protect.
   The migration is the v1→v2 step of
   `_ensure_namespace_keys_schema`, copying hash, nonce and
   expiry verbatim with idempotent upserts, and is safe to
   re-run.

   The legacy `namespace_attributes.keys` column is left in
   place but is neither read nor written from phase 2
   onward. Rollback therefore loses keys created after the
   migration, while keys that predate it are unaffected —
   the exposure is one upgrade cycle, it is documented in
   the operator guide's upgrade notes, and it matches the
   precedent accepted for `node_daemon_states`.

   The hot path improved rather than regressed:
   `verify_token` previously loaded a namespace's entire
   attributes row and walked every key in it, and now does a
   single point read served by the leading column of the
   `(namespace, name)` unique index. `/auth` additionally
   pushes the expiry filter into SQL, so it no longer
   bcrypt-compares keys it is going to reject. No caching was
   added, deliberately: a stale cache would delay nonce-based
   revocation, and the point read is already cheaper than
   what it replaced.
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

   **Resolved by phase 3 (2026-08-03).** Yes. Endpoints
   guarded by `caller_is_admin` now require **both** the
   `system` namespace and a `cluster-admin` scope, on top of
   the derived scope for the operation itself. Unscoped keys
   carry the wildcard and satisfy all of it, so existing
   administrative automation is untouched.

   The marker is `cluster-admin`, hyphenated rather than
   dotted, because it names no family and so no family
   wildcard can synthesise it. Of the twenty methods
   `caller_is_admin` guards, only two derive an `admin.*`
   scope; the rest derive `node.*`, `issuer.*`, `auth.*` and
   `blob.read`, which is exactly why a dotted `admin.*`
   would not have worked.

   Requiring both axes is what makes a least-privilege
   administrative credential expressible:
   `["cluster-admin", "node.read"]` grants cluster-wide
   visibility to a monitoring workload that provably cannot
   delete a node. A single all-or-nothing flag could not say
   that.
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

    **Resolved by phase 3 (2026-08-03).** In scope, and done
    first, as step 3a — before scopes existed, so that scope
    enforcement could be added to an already-universal path
    rather than being another thing to remember.
    Authentication moved onto
    `api_base.Resource.method_decorators`, the 120
    per-method `@api_base.verify_token` decorators were
    removed, and `@api_base.public` became the only way out.

    The measurement that justified the shape: 120 of the 124
    authenticated methods carried the decorator and the four
    that did not were the correct four. A good record, but
    the failure mode was wrong — forgetting it on a new
    endpoint left that endpoint silently open, and nothing
    would have caught it.

    `log_token_use` was deliberately **not** moved. Three of
    the 120 methods omit it on purpose and
    `AuthNamespacesEndpoint.post` writes its own richer
    events, so moving it would have double-logged there and
    made 3a something other than the pure refactor it needed
    to be.

    Backstopped two ways: a structural test enumerating
    `app.url_map` and asserting every method either
    authenticates or is explicitly `@public`, with the
    public set written down and individually justified; and
    `tools/check-endpoint-authentication.sh` as a pre-commit
    hook, modelled on the `from_db_by_ref` guard. The
    decorator ordering assumption — class-level decorators
    running outermost, so authentication precedes the
    ownership checks that assume an authenticated caller —
    is asserted by its own test rather than left as a
    comment.
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

    **Resolved by phase 3 (2026-08-03).** Scopes compose
    with trust, and the test exists:
    `shakenfist/tests/external_api/test_scope_trust_composition.py`.

    A key scoped `artifact.read` in a namespace that a cache
    namespace trusts can list the cache's artifacts and read
    them by UUID, and cannot delete them; a key scoped
    `instance.read` is refused outright rather than being
    handed an empty list; and a key granted nothing gains
    nothing from trust. Reading by UUID is asserted
    separately from listing because the two are separately
    guarded — and were separately wrong, see the artifact
    read bug below. Each
    refusal is paired with a control — a wildcard key
    reaching the same object across the same trust succeeds
    — so a 403 arriving for some unrelated reason cannot
    read as the property holding. Trust remains necessary as
    well as insufficient: the right scope without the trust
    grant sees nothing.

    The same suite drives a key the exchange actually
    minted, through the whole chain (issuer, rule, identity
    token, exchange, key, token), because "a federated key
    is just a namespace key" is the claim the design rests
    on and it is cheap to stop assuming it.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Terminology and glossary | [PLAN-auth-federation-phase-01-glossary.md](PLAN-auth-federation-phase-01-glossary.md) | Complete |
| 2. Namespace keys as first-class objects | [PLAN-auth-federation-phase-02-key-objects.md](PLAN-auth-federation-phase-02-key-objects.md) | Complete |
| 3. Federated exchange and scope enforcement | [PLAN-auth-federation-phase-03-exchange.md](PLAN-auth-federation-phase-03-exchange.md) | Complete |
| 4. Authentication documentation | [PLAN-auth-federation-phase-04-docs.md](PLAN-auth-federation-phase-04-docs.md) | Complete |
| 5. OIDC plan refresh | [PLAN-auth-federation-phase-05-oidc-plan-refresh.md](PLAN-auth-federation-phase-05-oidc-plan-refresh.md) | Complete |
| 6. Secrets that cannot be logged by accident | [PLAN-auth-federation-phase-06-secret-types.md](PLAN-auth-federation-phase-06-secret-types.md) | Complete |
| 7. Leak detection | [PLAN-auth-federation-phase-07-leak-detection.md](PLAN-auth-federation-phase-07-leak-detection.md) | In Progress |

Every open question above was resolved by phases 2 and 3, so
none needed carrying into phase 7.

Phases 6 and 7 came out of phase 2's step 2g, which removed
five separate sites that wrote credentials into audit
events. Four were known when the phase was planned; the
fifth was found only because the tests asserted the secret
appeared *nowhere* in any event rather than checking the
named field was gone. Two rounds of the same bug in one
phase is the argument for both: phase 6 makes the mistake
hard to make, phase 7 makes it detectable when it is made
anyway.

Phase 6 found two more sites, both worse than the five, and
found both by querying log aggregation for the credential —
which is the mechanism phase 7 proposes, used by hand.
Planning it found the sixth: `sf-queues` logs every
configuration item at INFO on startup, so `AUTH_SECRET_SEED`
and `MARIADB_PASSWORD` were written out in full and shipped
to Loki on every daemon start. Executing it found the
seventh, in the sweep step: `BlobTransfer.external_view()`
published the transfer's authorisation token, and every
caller of that method passes the result into an audit event
or a log line, so a live credential left the cluster on every
blob transfer. Both are fixed. See phase 6's survey and its
step 6f for the evidence.

That two of the seven were found by a standing query and
none by review is the strongest argument this plan has for
phase 7, and for building the log-sink half of it first.

Neither blocks phases 3–5. There was an ordering hazard here
— phase 7's secret format needed to be settled before phase
3 minted its first exchange key, or keys minted in between
would not match the scanners — and it is resolved by moving
the format into phase 3, which is where the first
cluster-generated secrets appear. Phase 7 keeps the
detection half, which has no such constraint.

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

Phase 3 shipped most of the developer and operator guide
halves of this as it went, because a security decision is
cheapest to write down while it is being made. What remains
is the user guide page. This section previously said that
page did not exist; it does, at 34 lines, and it predates
every one of phases 1 to 3, so it is a rewrite of live
content rather than a green field. Phase 4 should also
re-read the two existing guides end to end, rather than
assuming a series of incremental additions composes into a
coherent page.

Two of this section's other assumptions were also overtaken
by phase 3 and are corrected in the phase plan: the worked
GitHub Actions example shipped in the developer guide rather
than the operator guide, and key expiry and scopes are not
readable through any API or client, so the user guide cannot
describe how they "surface in `sf-client`". See the phase
plan's *What the survey found*.

### Phase 5: OIDC plan refresh

Rewrite `PLAN-oidc-authentication.md` (the human-login
sibling, a stub when this phase was planned) against the
as-built reality of phases 1–4, so it plans forward from
what exists rather than from the pre-federation codebase:

* Its Situation section describes key objects, scopes, the
  trusted-issuer configuration, and the exchange endpoint
  as existing infrastructure, with pointers to the
  glossary's terms.
* Its tentative phase 1 (JWT validation refactor) and the
  JWKS half of its tentative phase 2 are marked superseded
  by this plan's phase 3, and its remaining phases
  renumbered around what is genuinely left: interactive
  CLI flows, claim-driven multi-namespace authorisation,
  admin-as-a-claim, IdP worked examples, and functional
  testing. Note that the *discovery* half of its phase 2
  was **not** built and must keep a live row: a trusted
  issuer carries an operator-supplied `jwks_uri` and
  nothing fetches `.well-known/openid-configuration`.
  GitHub Actions never needed discovery because the
  workflow arrives holding a minted token, but a human
  client has to start a flow, and the endpoints it needs
  are exactly what a discovery document publishes. This
  was found by the phase 5 survey; the phase plan carries
  the detail.
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

### Phase 6: Secrets that cannot be logged by accident

Every credential leak step 2g fixed had the same shape:
`extra={'token': token}`, with the event layer coercing the
value to a string on the way out. Nothing in the type system
objected. The remedy is to make the secret types refuse to
render themselves.

`pydantic.SecretStr` already does exactly this — `str()` and
`repr()` of one yield `'**********'`, and the real value
comes back only from an explicit `.get_secret_value()` call.
The codebase is pydantic throughout, so this is a change of
field type rather than a new dependency. It is a new idiom
though: the phase 6 survey found no existing `SecretStr` use
anywhere in the tree.

* `NamespaceKeyAttributesData.key` and `.nonce` become
  `SecretStr`. So does anything else the sweep below turns
  up — the secret-carrying config fields are
  `AUTH_SECRET_SEED`, `MARIADB_PASSWORD` and
  `LOKI_AUTH_HEADER`.
* `schema/sqlalchemy.py`'s table generator learns that
  `SecretStr` maps to a string column, and the three-layer
  accessors unwrap on write and re-wrap on read, so the
  secret is wrapped everywhere above the database boundary.
  This mapping is not optional bookkeeping: the generator's
  fallback for an unrecognised type only logs a warning and
  returns `LONGTEXT`, so omitting it silently changes the
  table's DDL.
* Call sites unwrap explicitly at the points that genuinely
  need the plaintext. The phase 6 survey enumerates six, not
  the three originally listed here: `verify_token`'s nonce
  comparison, `/auth`'s bcrypt comparison, `create_token`'s
  JWT claim, two SQL writes and the gRPC converter pair.
  Each unwrap is a place a reviewer can look at and ask
  "should this value be here?", which is the whole point.
* A sweep for other unwrapped secret-carrying fields, and a
  test that a `SecretStr` field survives a round trip
  through the database without being stringified on the way.

Scope note: this would have caught four of step 2g's five
sites. It would *not* have caught the fifth, which logged
the raw HTTP request body before any model existed — that
one is structural and stays fixed by `handles_credentials()`
in `external_api/base.py`, a predicate over the request path
which both body loggers in `external_api/app.py` consult.
Type safety and the request-tracing redaction are
complementary, not alternatives.

This phase is independent of the rest of the federation work
and could be executed by someone who is not otherwise
following this plan. It is *not* discretionary in timing, as
this section previously implied: it closes a live credential
leak (see the Execution section above), and its first step
exists to stop that leak ahead of the type work.

### Phase 7: Leak detection

Phase 6 stops secrets reaching a sink. This phase assumes
one got out anyway and shortens the time to notice.

The credential *format* this phase was originally going to
define now lands in phase 3 instead, because phase 3 mints
the first cluster-generated key secrets and anything minted
before the format existed would need reissuing. Phase 3
therefore delivers the `sfk_` prefix, the CRC32 checksum,
the reservation of the prefix against operator-supplied
secrets, and early rejection on a bad checksum. What remains
here is detecting the format once it escapes.

* **A gitleaks rule** for the format. Shaken Fist has no
  gitleaks job yet — ryll's `ci.yml` has the working
  pattern, including that `gitleaks-action@v2` refuses to
  run on org repos without a paid licence so the upstream
  binary is invoked directly, and that gitleaks is only
  packaged from Debian 13 onward. Adding the job is part of
  this phase. The `secret-handling` consistency audit in
  `shakenfist/development` already requires a scanner in CI
  and lists Shaken Fist as non-compliant against
  `shakenfist/shakenfist#3546`, so this phase is also how
  Shaken Fist becomes compliant. (A parenthetical added on
  2026-08-16 claimed that audit did not exist. It does, and
  has since 2026-07-27; the phase 7 survey reported a false
  negative and the false correction is withdrawn.)
* **Log-sink detection, which is the valuable half.** Events
  go to syslog *and* to Loki, so a credential written into
  an event leaves the cluster and lands in log aggregation.
  A standing Loki query for the secret format across all
  streams would have caught every one of step 2g's five
  sites in production, automatically, without anyone
  thinking to look. A CI scanner only catches a secret
  someone committed to the repository, which is the less
  likely accident for a runtime-minted credential. Both are
  worth having; if only one gets built, build this one.
* **A verification pass** that the format phase 3 shipped is
  actually what the scanners match — one regression test
  asserting a freshly minted key is matched by the committed
  gitleaks rule, so the two cannot drift apart silently.

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
* Minted secrets no longer appear in audit events, and the
  secret-carrying types cannot be stringified into one by
  accident.
* A credential that escapes into syslog or Loki anyway is
  detectable by a standing query, because cluster-minted
  secrets carry a recognisable prefix and a verifiable
  checksum.
* A glossary exists in `docs/`, is linked from the three
  authentication guides, and this plan's terms are used
  consistently across code, CLI help, and docs.
* The code passes `pre-commit run --all-files` (flake8,
  stestr unit tests, mypy); new code follows the
  three-layer database pattern and Pydantic schema
  conventions; functional coverage exercises the exchange
  end-to-end in `shakenfist/deploy/shakenfist_ci/cluster_ci_tests`.
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
* **Restoring prune-on-write for expired keys**, if the
  cluster daemon's sweep proves too weak a guarantee. While
  phase 2 was in flight, develop independently fixed issue
  #3521: `get_api_token()` mints a short-lived
  `_service_key_*` every few minutes per daemon, and
  filtering those only on read let the `keys` JSON blob grow
  until it crossed gRPC's maximum message size, failing
  namespace reads cluster-wide. The fix purged expired
  entries on every write. Phase 2's cutover removes that
  code path. The original failure mode cannot recur — keys
  are rows now, so no single value grows, and the expiry
  filter is applied in SQL — but the guarantee is weaker in
  one respect: removal now depends on the cluster daemon
  running, where purging on write did not. The consequence
  if it never runs is bounded table growth rather than a
  cluster-wide read failure. `delete_expired_namespace_keys()`
  already exists if we decide the write path should sweep too.
* **`sf-client namespace add-key --expiry`**: phase 2 added
  the `expiry` body parameter to the key create and update
  endpoints, but the command line has no flag for it yet,
  so the REST API or the Python client must be used
  directly. A client-python change, hence not a phase of
  this plan.
* **`sf-client federation ...`**: phase 3 added three route
  families the client library does not wrap —
  `/auth/issuers`, `/auth/namespaces/{namespace}/rules` and
  `/auth/federated` — so operators and namespace owners
  configure federation with `curl` today, and the
  documentation is written that way. A client-python
  change, hence not a phase of this plan. The exchange
  itself is the least urgent of the three: a CI job wants a
  plain HTTP call it can make before it has installed
  anything, which is what it already has. Issuer and rule
  management is where a command line would actually earn
  its keep.
* **A readable view of a key**
  ([#3672](https://github.com/shakenfist/shakenfist/issues/3672)),
  found while planning phase 4. Phase 2 gave keys an expiry
  and phase 3 gave them scopes and provenance;
  `NamespaceKey.external_view()` renders all three and
  calls itself "the operator visible view of a key", and no
  endpoint calls it. `GET
  /auth/namespaces/{namespace}/keys` still answers with a
  list of key *names*, read from the legacy
  `keys['nonced_keys']` dict. So a namespace owner cannot
  ask which of their keys expires when, or what a federated
  key may do, without reading the database — which is the
  audit question provenance was added to answer. Unlike the
  two client-python items above this is server side, and it
  is a breaking change to a published response shape with
  in-tree consumers, so it needs a compatibility design of
  its own rather than an edit to the handler. That is why
  phase 4 documented the gap instead of closing it.
* **Rotating the credentials phase 6's survey found in Loki.**
  `AUTH_SECRET_SEED` and `MARIADB_PASSWORD` have been shipped
  to log aggregation in plaintext by every `sf-queues`
  startup, so they must be treated as disclosed to anyone
  with log read access. Phase 6 stops the leak; it cannot
  un-leak them. Rotating the seed invalidates every
  outstanding token cluster-wide, which is a deliberate
  operator action rather than something a phase does, and
  purging the existing log entries is a Loki retention
  question.

  The **guidance** half of this is now written.
  `docs/operator_guide/credential_rotation.md` records the
  disclosure, gives the LogQL to confirm it on a given
  cluster, and covers the rotation procedure and blast
  radius for each of the three affected options; it is
  linked from `upgrades.md` and `logging.md` so an upgrading
  operator meets it. That was added while addressing review
  on the phase 6 PR, on the argument that a plan file's
  Future work list does not reach operators. What remains
  outstanding is the **act** of rotating on any given
  deployment, which is the deployer's call.
* **`BlobTransfer.token` as a `SecretStr`.** Phase 6's sweep
  found this field is a bearer credential -- the transfers
  daemon compares it against what an inbound connection
  presents before sending blob data -- and that
  `external_view()` published it into two audit events and
  the transfers daemon's log fields, so it was reaching Loki
  on every blob transfer. Phase 6 removed it from
  `external_view()`, which closes every path it was
  escaping by, and stopped there: wrapping the field itself
  touches about fourteen sites across `mariadb.py`, the
  database daemon, `blob.py` and the transfers daemon, which
  is a change the size of phase 6's own step 6c and not what
  the sweep step was scoped for. Worth doing for the same
  reason the namespace key fields were done, just not as an
  afterthought to a documentation step.
* **Secret material in `util/vdi_tokens.py`.** The Kerbside
  signing key's private PEM is handled as plain strings
  inside a dict stored in a `cluster_config` row. It is
  protected today by convention plus the row name ending in
  `_KEY` so `SECRET_CONFIG_KEY_RE` masks it in
  `show-config`, and its module docstring is explicit that
  private key material must never be logged, evented or
  served. Nothing found it leaking. Wrapping it means
  restructuring that dict rather than changing a field type,
  hence deferred. Note separately that `load_cluster_config()`
  pushes every `cluster_config` row into the environment of
  every daemon, so the private key is present in each
  daemon's environ -- a different exposure surface from
  logging, and one this plan has not examined.
* **Wrapping the minted plaintext key secret.** Phase 6
  Decision 6 leaves `credentials.generate()`'s output a plain
  `str`. It is the one value in the system which is an actual
  bearer credential rather than a hash, but it must reach the
  HTTP response body, and an unwrap in the response
  serialiser fails by rendering `**********` into the
  operator's only copy of the credential — silent and
  destructive. Revisit if the response path ever gains a
  typed serialiser where the unwrap can be made structural.
* **mypy coverage for the authentication modules.**
  `namespace.py`, `namespace_key.py` and
  `external_api/auth.py` are absent from the mypy rollout in
  `tox.ini`, which is why phase 6's field conversion has to
  be verified by reading rather than by the type checker.
  These three carry the credential paths and are good
  candidates for the next tranche of the rollout.
* **The `secret-handling` audit's reference invocation** in
  `shakenfist/development` was scoped to every ref rather
  than to `HEAD`, which is slow, noisy and — under gitleaks
  8.16 — misattributed. Fixed there in `fd4ddc4` as part of
  phase 7, along with guidance on positive controls and on
  how to accept a finding that cannot be removed. Four other
  projects still carry the unscoped invocation: `ryll`
  (`ci.yml`, which additionally lets the scanner skip
  docs-only changes), `instar` and `client-python-k3s`
  (`supply-chain.yml`), and `sfui` (`gitleaks.yml`). Each
  needs a small pull request. While there, note that
  `PROJECT-CONSISTENCY-AUDITS.md`'s security table still
  lists Shaken Fist's GitHub secret scanning as Disabled,
  which `PLAN-consistency.md` records as having been enabled.
* **Token introspection / jti denylist** if bounded-delay
  revocation of *scoped keys themselves* (as opposed to
  their derived tokens) ever proves insufficient.
* **Templated mapping rules** with namespace auto-creation,
  per open question 3, if per-repo rule sprawl becomes
  real.

### Bugs fixed during this work

Phase 3:

* **Cross-namespace artifact reads by UUID**, found while
  writing phase 3's trust composition test. Unrelated to
  federation and older than this plan, but fixed on this
  branch rather than filed, because an issue would have
  advertised the hole before a fix existed.

  `arg_is_artifact_ref` short-circuits a UUID straight to
  `Artifact.from_db`, applying no namespace filter — that is
  deliberate, because the same decorator serves system
  callers who legitimately reach across namespaces. It makes
  `requires_artifact_access` the only guard on the path, and
  that guard read `if a.shared and requestor not in
  [a.namespace, 'system']: 404`, which is inverted in both
  directions. Unshared artifacts belonging to any namespace
  were readable by anyone who knew the UUID, and shared
  artifacts were refused to precisely the namespaces they
  had been shared with. The refusal branch then called
  `LOG.with_object`, which `shakenfist_utilities` no longer
  provides, so the one case it did refuse got a 500 rather
  than a 404 — evidence that the branch had not executed in
  a long time.

  The fix replaces the restated predicate with the one the
  artifact listing already filters on,
  `namespace_or_shared_filter`: owner, a namespace which
  trusts the caller, system, or shared. "Appears in the
  list" and "is readable by UUID" are now one rule rather
  than two copies of a rule. Four routes were affected: the
  artifact itself, its events, its versions and its cluster
  operations.

* **Artifact names would not resolve to shared or trusted
  artifacts.** `docs/user_guide/objects.md` has long said a
  by-name lookup searches everything visible to the caller,
  including shared artifacts. It did not:
  `arg_is_artifact_ref` handed `from_db_by_ref` the caller's
  own namespace, so a tenant could read a shared image's
  name out of `GET /artifacts` and then get a 404 asking for
  it by that name.

  `Artifact.from_db_by_ref_visible_to` resolves in two
  phases. The first is exactly `from_db_by_ref` against the
  caller's own namespace, so whatever that resolves to still
  wins; only on a miss does it widen to what
  `namespace_or_shared_filter` admits. The ordering is the
  part that matters — without it, sharing an artifact named
  `debian-11` would silently retarget every tenant who
  already had one. This mirrors `Artifact.from_url`, which
  has resolved URLs by the same "everything visible, prefer
  local" rule since `9faa90c71`, so the two resolution paths
  now agree.

  Widening applies to reading only. The ref decorator split
  into `arg_is_visible_artifact_ref` (paired with
  `requires_artifact_access`) and `arg_is_artifact_ref`
  (paired with `requires_artifact_ownership`), so a name
  cannot resolve into another namespace on a route which
  then changes what it found.

* **A namespace trust authorised artifact mutation.**
  `requires_artifact_ownership` tested
  `namespace_is_trusted`, so a trusted namespace could
  delete, share, unshare, retag and rewrite the metadata of
  the trusting namespace's artifacts. It now tests
  `request_namespace() not in [a.namespace, 'system']`,
  which is what `requires_instance_ownership` and
  `requires_network_ownership` have always used; artifacts
  were the one object type where trust reached past reading.
  Creating an object *in* a namespace which trusts you is
  untouched, so the operator guide's `ci-images` "gifting"
  pattern still works.

  This is a behaviour change for anyone whose tooling
  deleted artifacts across a trust; they need a key in the
  owning namespace, or system. Recorded in the v0.7 to v0.8
  release notes.

Phase 2:

* **Credentials in audit events**, five sites (step 2g).
  `create_token()` logged the whole minted JWT and the
  nonce; `log_token_use()` logged the presented JWT; both
  namespace-creation events logged the invoking JWT; the
  malformed-key event logged the key body, which held the
  stored hash and the nonce. The fifth site was found while
  testing the other four: the API request-tracing events in
  `external_api/app.py` logged request and response bodies
  verbatim, so `POST /auth` recorded the namespace's
  *plaintext* key inbound and the minted token outbound.
  Bodies are no longer logged for any route under `/auth`.
* **Two unreachable bugs in the key update endpoint** (step
  2e): the membership test ran one dict level too high so
  every update reported an unknown key, and a namespace name
  was passed where the `Namespace` object was expected.
  Neither was reachable because nothing tested `PUT`; both
  are pinned now.
* **Swagger examples naming a non-existent `key_names`
  field** (step 2e); the field is `keys`.
* **Silent accumulation of expired keys** (step 2f), which
  previously stayed in the `nonced_keys` dict forever.
* **Stale-hash clobbering in the migration** (step 2d): a
  blind upsert would have written the JSON column's stale
  hash over a key rotated since the migration first ran.
  Caught before it shipped, but it would have silently
  reverted a rotation.

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
