# Phase 3 — federated exchange and scope enforcement

Parent plan:
[PLAN-auth-federation.md](PLAN-auth-federation.md).

## Scope

Phase 3 is the point of the whole plan: a GitHub Actions workflow
holding nothing but its own OIDC identity token can exchange it for a
scoped, expiring namespace key on a Shaken Fist cluster, and that key
can do exactly what it was granted and nothing else.

Getting there needs two halves that are independent until the last
step. The **enforcement** half gives every token a set of scopes and
checks them on every request. The **issuance** half validates an
external identity token against a standing, claim-gated rule and mints
a key. Enforcement is built first, because a rule that grants scopes
is meaningless until scopes are enforced, and because building it
first means the federated path is never the only thing exercising it.

The phase covers:

- Inverting authentication from opt-in to opt-out, so a forgotten
  decorator fails closed rather than silently opening an endpoint.
- A scope vocabulary, mechanically derived, enforced on the universal
  authentication path.
- `TrustedIssuer` and `MappingRule` objects, with CRUD APIs.
- Identity token validation against cached JWKS.
- The exchange endpoint, with the abuse resistance an unauthenticated
  endpoint needs.
- Proof that scopes survive the namespace trust boundary.

Deliberately **not** in this phase: `sf-client federation ...`
commands, which live in the client-python repository and follow as
their own change; the CI conductor integration; and the cache
save/restore actions.

## Design

### Scope vocabulary and derivation (resolves open question 1)

A scope is a `<family>.<verb>` string. Operators see three verbs:

| Verb | Meaning | Derived from |
|------|---------|--------------|
| `read` | Observe without changing | `get`, `head` |
| `write` | Create or modify | `post`, `put`, `patch` |
| `delete` | Destroy | `delete` |

The family comes from the resource class, and the verb from the
flask-restful method name — which *is* the HTTP verb, so nothing needs
to consult routing. `BlobEndpoint.get` derives `blob.read` with no
decoration at all. Coverage is therefore automatic across all 124
methods, which is what dissolves open question 2 into "audit the
override list".

Two escape hatches, both explicit at the decoration site so they are
greppable:

```python
class InstanceRebootEndpoint(api_base.Resource):
    @api_base.scope(verb='power')       # -> instance.power
    def post(self, ...): ...

class SomeOddEndpoint(api_base.Resource):
    scope_family = 'artifact'           # class name is unhelpful
```

The wildcard scope `*` means "everything", and is what legacy
(unscoped) keys carry, so nothing an operator has today changes
behaviour.

**Growing the vocabulary.** A new verb is added only when an operator
genuinely needs to grant that action separately from `write`. The test
is whether anyone would sensibly write a rule granting it alone.
`instance.power` passes; `instance.reboot-hard` does not.

### Enforcement inversion (resolves open question 10)

Measured on the current tree: 124 HTTP methods across 89 resource
classes, of which **120 already carry `@verify_token`**. The four
which do not are `Root.get`, `Livez.get`, `Readyz.get` and
`AuthEndpoint.post` — exactly the endpoints that should be public.
There is no existing hole; the risk is the next endpoint someone
writes.

`api_base.Resource` already has a `method_decorators` list, and
class-level decorators run outermost, so authentication correctly
precedes the per-method ownership checks. Authentication and scope
enforcement move there, the 120 per-method `@verify_token` decorators
are removed, and the four public endpoints gain `@api_base.public`.

The audit inverts from "did every endpoint remember auth?" to "is
every `@public` justified?", which is four lines to review. A
pre-commit check backstops it, following the `from_db_by_ref`
namespace-scoping hook precedent already in
`.pre-commit-config.yaml`.

The structural test worth more than any of this: enumerate every route
Flask has registered and assert each one either enforces
authentication or appears in the `@public` allowlist. That test makes
the guarantee true by construction rather than by review.

### Admin endpoints (resolves open question 9)

`caller_is_admin` today checks only that the request namespace is
`system`. A key scoped to `blob.read` but minted into `system` would
pass every admin endpoint — a scoped credential escalating to full
cluster administration.

Admin endpoints will require **both** the `system` namespace and an
`admin` scope on the token. Legacy unscoped keys carry the wildcard,
so existing admin automation is unaffected; only a deliberately scoped
system-namespace key is constrained, which is the entire point.

### Object model (resolves open question 3's leftovers)

Both objects follow the `NamespaceKey` recipe phase 2 established:
Pydantic static/attribute schemas, three-layer MariaDB accessors,
protos, a `DatabaseBackedObject` with state machine and events,
registration in `OBJECT_NAMES_TO_CLASSES`.

**`TrustedIssuer`** — cluster-level, `system`-owned. Who may vouch for
identities here is an admin decision.

| Field | Notes |
|-------|-------|
| `name` | Unique; how rules reference it |
| `issuer_url` | Must match the token's `iss` exactly |
| `jwks_uri` | Where signing keys are fetched |
| `audience` | Expected `aud` |

**`MappingRule`** — owned by the namespace it targets, created under
the same gate as `add-key` (`requires_namespace_ownership`, which
`auth.py` already defines and uses for key creation).

| Field | Notes |
|-------|-------|
| `namespace` | Owner; rule dies with it |
| `name` | Unique within the namespace |
| `issuer` | Reference to a `TrustedIssuer` |
| `bound_claims` | Claim name → matcher (see below) |
| `scopes` | Granted to keys minted through this rule |
| `key_ttl` | Seconds; becomes the minted key's expiry |
| `key_name_prefix` | Human-readable prefix for minted key names |

Two things the master plan left for this phase:

- **Multiple rules per namespace may bind the same issuer.** Two
  repositories legitimately feed one cache namespace, and forbidding
  it would push operators toward one over-broad rule — the opposite of
  what we want.
- **Mutating a rule does not touch keys already minted from it.** Keys
  stand alone once minted; provenance records the rule reference *and*
  the claims that were satisfied, so the audit trail describes the
  grant as it was, not as the rule reads today. Shortening a rule's
  scopes does not retroactively narrow a live key — delete the key if
  that is what you mean.

### Claim matching

This is where OIDC federations get compromised, so the semantics are
deliberately narrow. `bound_claims` maps a claim name to one of:

| Matcher | Form | Example |
|---------|------|---------|
| Exact | `"value"` | `"repository": "shakenfist/ryll"` |
| Enumerated | `["a", "b"]` | `"ref": ["refs/heads/develop", "refs/heads/main"]` |

Every bound claim must be present and must match, and matching is
exact string comparison — no globbing, no regular expressions, no
prefix matching. A rule with no bound claims is rejected at creation:
it would grant any holder of any token from that issuer.

Patterns are deliberately absent from v1. `repository: shakenfist/*`
looks reasonable until someone registers `shakenfist-evil`, and the
anchored-pattern rules needed to make it safe are exactly the thing
reviewers get wrong. If enumerated alternatives prove insufficient in
practice, patterns can be added later with anchoring enforced at rule
creation — but they must not be the default.

### The exchange

`POST /auth/federated`, unauthenticated by nature, body
`{token, namespace, rule}`. The order of operations is chosen so that
the cheapest rejections happen first:

1. Reject if the body exceeds `FEDERATION_MAX_TOKEN_BYTES`. No
   parsing.
2. Parse the JWT header and claims **without verifying** to read
   `iss`. Reject if no `TrustedIssuer` matches. No network yet.
3. Rate limit per source address.
4. Verify the signature against cached JWKS.
5. Check `aud`, `exp`, `nbf`.
6. Refuse if this `(jti, rule)` pair has been seen.
7. Load the named rule in the named namespace; check bound claims.
8. Mint the key.

Steps 1 and 2 preceding step 4 matter more than they look.
`PyJWKClient` fetches synchronously inside the request, so an
unfiltered path would let anyone with a made-up `iss` tie up a
gunicorn worker on an outbound HTTP call.

**JWKS caching** uses `PyJWKClient` (PyJWT 2.13.0 is already a
dependency — no new package), with `cache_jwk_set` and an explicit
`lifespan`. An unknown `kid` triggers at most one refetch, guarded so
concurrent requests for the same issuer collapse into a single fetch
rather than a thundering herd against the IdP.

**Replay** is refused per `(jti, rule)`, not per `jti`: exchanging one
token against two rules to reach two namespaces is a legitimate
pattern the CI conductor design depends on, while re-exchanging the
same token against the same rule is not. Seen pairs are stored in a
small table with the inbound token's `exp` as their own expiry, and a
unique index on `(jti, rule_uuid)` does the arbitration — the insert
failing *is* the replay detection, with no read-then-write race. They
are reaped like any other expiring row.

**Rate limiting** is per source address, backed by MariaDB so the
limit is cluster-wide rather than per gunicorn worker. Request volume
on this endpoint is low by nature (once per CI job), so a row per
source per window is affordable.

**Auditing.** A successful exchange writes an audit event against the
minted key and its namespace, carrying the satisfied claims and the
rule, never the secret. A *failed* exchange writes an event against
the rule's owning namespace: a stream of near-miss claim failures is
what probing looks like, and the namespace owner is the person who
needs to see it. Failures against an unknown namespace or rule cannot
be audited to an owner and are logged only, to avoid giving an
unauthenticated caller a way to write unbounded events.

### Minted key naming (resolves open question 5)

The rule's `key_name_prefix` is a prefix, not a template, and the
exchange appends a random discriminator — the same shape as
`_service_key_<random>`. Names are therefore unique by construction
and there is no collision case to resolve: a workflow re-run gets a
new key rather than silently rotating the secret out from under a
still-running job.

Federated keys appear in the legacy `keys` listing like any other key.
Hiding them would make audits lie.

### Token lifetime (resolves open question 6)

Mint-time `expires_delta` is capped at the key's remaining lifetime.
The nonce check already invalidates derived tokens the moment the key
expires, so this is cosmetic — but a token whose `exp` outlives the
credential it came from is confusing to anyone reading it, and the cap
costs one `min()`.

### Scopes and trust (resolves open question 11)

Namespace trust grants cross-namespace *visibility*. It must not grant
*capability*. A token scoped `blob.read` may read blobs it can see,
including those visible through trust, and must never gain wildcard
behaviour because the object it touched lives in a trusting namespace.

This gets an explicit test: a scoped key in namespace A, a trust from
B to A, and an assertion that the scoped token can read across the
trust but cannot write across it. Without that test, trust becomes a
scope-escape hatch.

### Authentik as the second issuer

No code differs. An Authentik `client_credentials` service account
presents a JWT with `iss` of the Authentik realm, `aud` of the
configured client, and claims such as `sub` and `groups`. A rule binds
`{"groups": ["sf-ci"]}` instead of
`{"repository": "shakenfist/ryll"}`. The phase plan's proof obligation
is a test constructing such a token against a mock issuer and
exchanging it successfully — demonstrating the machinery is
issuer-generic without shipping an Authentik dependency.

### Decisions

1. **Scopes are derived, not tagged** (operator, 2026-07-29).
   Automatic coverage beats readability-by-hand; overrides are
   explicit and greppable.
2. **Authentication inverts to opt-out in this phase** (operator,
   2026-07-29). Measured as cheap: 120 of 124 methods already
   authenticate, and the four exceptions are the correct ones.
3. **Admin endpoints require an `admin` scope** (operator,
   2026-07-29). Closes scoped-key escalation inside `system`.
4. **Full abuse resistance in v1** (operator, 2026-07-29). The
   endpoint is unauthenticated by nature and is the most exposed
   surface in the API.
5. **Exact and enumerated claim matching only.** No patterns in v1.
6. **Rules may share an issuer; mutating a rule does not affect
   already-minted keys.**
7. **Minted key names carry a random discriminator**, so re-runs never
   collide and never rotate a live key.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | high | opus | none | Enforcement inversion, no scopes yet. Move `verify_token` (and `log_token_use`) onto `api_base.Resource.method_decorators`; remove the 120 per-method `@api_base.verify_token` decorators; add an `@api_base.public` marker and apply it to exactly `Root.get`, `Livez.get`, `Readyz.get`, `AuthEndpoint.post`. Class-level decorators run outermost so auth precedes ownership checks — verify that ordering with a test, it is the load-bearing assumption. Add the structural test: enumerate `app.url_map` and assert every rule's methods either authenticate or are `@public`. Add a pre-commit check modelled on `tools/check-from-db-by-ref-namespace.sh` that fails if a resource method is added without either. No behaviour change intended: the full existing suite must pass unmodified. Commit subject: "api: authenticate every endpoint by default." |
| 3b | high | opus | none | Scope vocabulary and enforcement. Derivation (`<family>.<verb>` from resource class and method name, families defaulting from the class name with a `scope_family` class attribute override, verbs `read`/`write`/`delete`); the `@api_base.scope(verb=..., family=...)` annotation for overrides; enforcement on the same universal path added in 3a; wildcard `*` for tokens minted from unscoped keys; default-deny where derivation is impossible. Add the `admin` scope requirement to `caller_is_admin` per Decision 3. Publish the vocabulary and derivation rule in the developer guide. Tests: derivation for each verb, override honoured, wildcard passes everything, scoped token denied outside its scopes, and an admin endpoint refused to a scoped `system` key. Commit subject: "auth: derive and enforce token scopes." |
| 3c | medium | opus | none | `TrustedIssuer` object, following the `NamespaceKey` recipe exactly (`shakenfist/namespace_key.py`, `schema/namespace_key_data.py`, the `mariadb.py` three-layer accessors, `protos/database.proto`, `daemons/database/main.py` handlers, `OBJECT_NAMES_TO_CLASSES`). System-namespace-only CRUD endpoints under `/auth/issuers`. Unique on `name`. Run `tox -e genprotos`, never `grpc_tools` directly. Commit subject: "objects: add the TrustedIssuer object." |
| 3d | medium | opus | none | `MappingRule` object, same recipe, owned by its namespace, unique on `(namespace, name)`. CRUD under `/auth/namespaces/{namespace}/rules`, gated by `requires_namespace_ownership` (already defined in `external_api/auth.py` and used by key creation). Claim matcher validation at creation: exact strings or lists of strings only, at least one bound claim, referenced issuer must exist. Rules are deleted with their namespace. Commit subject: "objects: add the MappingRule object." |
| 3e | medium | opus | none | Identity token validation, no endpoint yet. A `shakenfist/federation.py` module: unverified header/claim peek to read `iss`; issuer lookup; `PyJWKClient` with `cache_jwk_set` and a configured `lifespan`; single-flight refetch on unknown `kid` (a lock per issuer, so concurrent requests collapse to one fetch); `aud`/`exp`/`nbf` verification; claim matching against a rule. Pure functions plus one cache object, no Flask. Tests use locally generated RSA keys and a mock JWKS endpoint — no network. Cover: good token, bad signature, wrong `aud`, expired, unknown `kid` refetch, refetch happens once under concurrency. Commit subject: "federation: validate identity tokens against trusted issuers." |
| 3f | high | opus | none | The exchange endpoint. `POST /auth/federated`, `@public`, body `{token, namespace, rule}`, implementing the eight-step order in the Design section. Mint via `NamespaceKey.new()` with the rule's scopes, `key_ttl` as expiry, provenance recording the rule reference and satisfied claims, and a random discriminator on the name. Audit events per the Design section, including the failed-exchange event against the rule's owning namespace and the deliberate silence when no owner can be identified. Response `{namespace, key_name, key}`. Config: `FEDERATION_MAX_TOKEN_BYTES`, `FEDERATION_JWKS_CACHE_SECONDS`. Commit subject: "auth: exchange identity tokens for scoped namespace keys." |
| 3g | medium | opus | none | Abuse resistance: the `(jti, rule_uuid)` replay table with a unique index (the failing insert *is* the detection — no read-then-write race), expiring at the inbound token's `exp` and reaped by the cluster daemon alongside expired keys; per-source rate limiting backed by MariaDB so the limit is cluster-wide rather than per worker, with `FEDERATION_RATE_LIMIT_PER_MINUTE` and `0` to disable. Tests: replay refused, same token against a second rule allowed, rate limit trips and recovers, reaper removes expired jti rows. Commit subject: "federation: replay and rate limit protection for the exchange." |
| 3h | medium | opus | none | Closeout. The trust-composition test from open question 11 (scoped key in A, trust B→A, read allowed across the boundary, write refused). The Authentik proof test: a mock issuer with `groups` claims, a rule binding them, a successful exchange with no code differing from the GitHub path. End-to-end functional coverage in `shakenfist/deploy/cluster_ci`. Docs: the scope vocabulary and derivation rule, the exchange flow, issuer and rule configuration, and a worked GitHub Actions example written against public GitHub concepts only — nothing about the private CI conductor. Glossary entries for trusted issuer, mapping rule and scope stop being future tense. Master plan open questions 1, 2, 3, 4, 5, 6, 9, 10, 11 marked resolved; phase 3 → Complete in the Execution table and `docs/plans/index.md`. Note the `sf-client federation ...` client-python follow-up in Future work. Commit subject: "docs: federated identity exchange." |

After each step the management session runs
`pre-commit run --all-files`, reads the diff against the brief, and
confirms no unrelated edits. After 3a additionally: the full existing
suite passed *unmodified*, since 3a is a pure refactor. After 3b: a
manual read confirming no endpoint lost enforcement. After 3f and 3g:
present the diff for operator review before commit — the exchange is
the security boundary of this whole plan. After 3h:
`tox -e genprotos` is a no-op against the committed tree.

## Risks and mitigations

- **Risk:** 3a touches 120 methods and silently drops enforcement
  somewhere.
  **Mitigation:** it is a pure refactor, so the existing suite must
  pass unmodified; plus the structural route-enumeration test, which
  makes the property true by construction rather than by inspection.
- **Risk:** the decorator ordering assumption is wrong and
  authentication ends up running *after* an ownership check that
  itself assumes an authenticated caller.
  **Mitigation:** 3a's brief calls it out as load-bearing and requires
  a test asserting the order.
- **Risk:** scope derivation silently mislabels an endpoint, granting
  more than an operator expects — the failure is quiet because it
  looks like it works.
  **Mitigation:** the override list is one grep; 3h's documentation
  publishes the derived scope for every endpoint family so it can be
  reviewed as data rather than inferred from code.
- **Risk:** claim matching is too narrow for real GitHub usage and
  operators route around it with an over-broad rule.
  **Mitigation:** enumerated alternatives cover the common
  "several branches, several repos" cases. If practice proves
  otherwise, anchored patterns are addable later — the danger is
  shipping them as the default, not having them at all.
- **Risk:** the exchange endpoint becomes a denial-of-service vector
  against either us or the IdP.
  **Mitigation:** Decision 4's full ordering, with size and allowlist
  checks before any network call, and single-flight JWKS refetch.
- **Risk:** a scoped key minted into `system` escalates.
  **Mitigation:** Decision 3. Additionally, 3d should consider
  warning at rule creation when the target namespace is `system`.
- **Risk:** trust becomes a scope-escape hatch.
  **Mitigation:** open question 11's test, which is a named
  deliverable of 3h rather than an afterthought.

## Definition of done

- [ ] Every registered route either authenticates or is one of a
      small, individually justified `@public` set, asserted by a test
      over `app.url_map` and backstopped by a pre-commit check.
- [ ] Scopes derive automatically for all endpoints; overrides are
      explicit, greppable and documented; legacy unscoped keys carry
      the wildcard and behave exactly as before.
- [ ] Admin endpoints require both the `system` namespace and the
      `admin` scope.
- [ ] `TrustedIssuer` and `MappingRule` are database-backed objects
      with events, CRUD APIs, and the correct ownership gates; rules
      die with their namespace.
- [ ] A GitHub Actions OIDC token can be exchanged for a scoped,
      expiring key that works with an unmodified `sf-client`.
- [ ] The same machinery exchanges an Authentik-style token with
      configuration only, proven by test.
- [ ] Replay of one token against one rule is refused; the same token
      against a different rule still works.
- [ ] Scopes survive the trust boundary: readable across, not
      writable across, asserted by test.
- [ ] No secret appears in any event; the failed-exchange event
      reaches the rule owner.
- [ ] `pre-commit run --all-files` clean; `tox -e genprotos` no-op;
      unit tests green; functional CI green on the branch **before**
      the PR merges.
- [ ] Master plan open questions 1, 2, 3, 4, 5, 6, 9, 10 and 11
      marked resolved; phase status updated in the Execution table
      and `docs/plans/index.md`; glossary and guides updated.

## Back brief

Before executing any step of this phase, the implementing sub-agent
must back-brief the management session on its understanding of the
brief and surrounding context. The management session must present
the 3f and 3g diffs for operator review before commit — the exchange
endpoint is the security boundary this entire plan exists to create.
