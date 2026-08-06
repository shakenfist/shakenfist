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
- A recognisable format for cluster-minted key secrets, absorbed from
  what was phase 7, so that the first federated key ever minted
  already carries it.
- The exchange endpoint, with the abuse resistance an unauthenticated
  endpoint needs.
- Proof that scopes survive the namespace trust boundary.

Deliberately **not** in this phase: `sf-client federation ...`
commands, which live in the client-python repository and follow as
their own change; the CI conductor integration; the cache
save/restore actions; and the *detection* half of the old phase 7 —
the gitleaks CI job, the custom scanner rule and the Loki query.
Detection is independent of the exchange and stays its own phase; the
credential *format* it detects is not, because keys minted before the
format exists would need reissuing.

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
behaviour. `<family>.*` means every verb in one family, matched on the
family rather than on characters so that `node.*` cannot reach
`nodegroup.read`.

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

Admin endpoints will require **both** the `system` namespace and a
`cluster-admin` scope on the token. Legacy unscoped keys carry the
wildcard, so existing admin automation is unaffected; only a
deliberately scoped system-namespace key is constrained, which is the
entire point.

The scope is hyphenated rather than dotted because it is not a
`<family>.<verb>` and names no family: of the twenty methods
`caller_is_admin` guards, only two derive an `admin.*` scope and the
rest derive `node.*`, `issuer.*`, `auth.*` and `blob.read`. Spelling
it `admin` invited reading it as the admin family's wildcard, which
was wrong in both directions — too narrow for what it gates, too
broad for what it grants. Carrying no dot also means no family
wildcard can synthesise it.

Both axes are required, and that is the feature rather than the
friction. `["cluster-admin", "node.read"]` is a monitoring credential
with cluster-wide visibility that provably cannot delete a node, and
that is not expressible if administration is one all-or-nothing flag.

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
2. Rate limit per source address.
3. Parse the JWT header and claims **without verifying** to read
   `iss`. Reject if no `TrustedIssuer` matches. No network yet.
4. Verify the signature against cached JWKS.
5. Check `aud`, `exp`, `nbf`.
6. Load the named rule in the named namespace; check bound claims.
7. Refuse if this `(token, rule)` pair has been seen.
8. Mint the key.

Steps 1 to 3 preceding step 4 matter more than they look.
`PyJWKClient` fetches synchronously inside the request, so an
unfiltered path would let anyone with a made-up `iss` tie up a
gunicorn worker on an outbound HTTP call.

Steps 2 and 3 were also the other way around when this section was
first written, on the reasoning that refusing an unknown issuer first
kept the rate limit table from growing a row per made-up `iss`. That
reasoning does not survive being stated: resolving the issuer is a scan
of every configured issuer with two reads per row, so the ordering
avoided one cheap insert by permitting an unbounded number of reads
above the meter. The review that caught it is recorded below; the meter
is now second, and only the argument checks sit above it.

Steps 6 and 7 were the other way around when this section was first
written, which is not implementable: the pair being claimed is
`(token, rule_uuid)`, and there is no rule uuid until the rule has
been read. Ordering the replay claim *last*, after claim matching and
after the namespace check, is also the behaviour worth having — a
refusal for any other reason must not consume the token's single use,
or an operator who fixes a rule cannot retry with a token that is
still perfectly valid. Being the last gate before minting is what
makes it effective against concurrency: two simultaneous
presentations of one token cannot both get past it, because the
second one's insert collides with the first one's.

**JWKS caching** uses `PyJWKClient` (PyJWT 2.13.0 is already a
dependency — no new package), with `cache_jwk_set` and an explicit
`lifespan`. An unknown `kid` triggers at most one refetch, guarded so
concurrent requests for the same issuer collapse into a single fetch
rather than a thundering herd against the IdP.

**Replay** is refused per `(token, rule)`, not per token: exchanging one
token against two rules to reach two namespaces is a legitimate
pattern the CI conductor design depends on, while re-exchanging the
same token against the same rule is not. Seen pairs are stored in a
small table with the inbound token's `exp` as their own expiry, and a
composite primary key on `(token_id, rule_uuid)` does the arbitration
— the insert failing *is* the replay detection, with no
read-then-write race. They are reaped like any other expiring row.

`token_id` is the token's `jti` where the issuer provides a usable
one, and a SHA-256 of its signature otherwise. Not every identity
provider stamps a `jti` — the claim is optional in the spec — and
refusing those outright would rule out conforming issuers, while
letting them through unprotected would leave open exactly the hole
this table exists to close. The signature is unique per token by
construction, so it identifies the token just as well; it is hashed
rather than stored because it is the secret half of the credential.

**Rate limiting** is per source address, backed by MariaDB so the
limit is cluster-wide rather than per gunicorn worker. Request volume
on this endpoint is low by nature (once per CI job), so a row per
source per window is affordable. Windows are fixed rather than
sliding: the boundary lets a determined caller send two allowances
back to back, which at this volume is not worth what a sliding window
costs in rows. Both this counter and the replay claim fail *closed* —
a database error refuses the exchange with a 503 rather than being
read as "under the limit" or "not seen before", since both of those
readings authorise something.

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

### Cluster-minted credential format (absorbed from phase 7)

Phase 3 is where Shaken Fist starts generating key secrets rather than
only accepting operator-chosen ones, so this is where the generated
form gets a shape. Doing it later would mean every key minted in
between needs reissuing before a scanner could recognise it.

Following the pattern GitHub (`ghp_`), GitLab (`glpat-`), Stripe
(`sk_live_`) and Slack (`xoxb-`) use:

```
sfk_<32 chars base62 random><6 chars base62 CRC32 checksum>
```

42 characters total, comfortably inside the existing 72-character
limit that `auth.py` enforces. The random body carries ~190 bits. The
prefix makes a leaked key greppable; the checksum lets a scanner
reject lookalikes without calling us, which is what makes scanning at
volume tolerable rather than alert spam.

This costs nothing cryptographically. A bearer token is a random
identifier, not ciphertext, so a fixed prefix is a label beside the
random part rather than a revealed piece of it — the entropy of the
random body is unchanged.

**The prefix must be reserved.** `/auth` cannot tell which stored key
a presented secret is meant to match until it bcrypt-compares against
each one, so "reject early on a bad checksum" is only sound if no
legitimate secret can carry the prefix and fail the checksum. Key
creation therefore rejects operator-supplied secrets beginning with
`sfk_`, mirroring how `_service_key` is already reserved for key
*names* in `auth.py`.

That reservation has one upgrade consequence which must reach the
operator guide rather than being discovered: **an existing key whose
secret happens to begin with `sfk_` will stop authenticating** once
early rejection is live, and must be rotated. The probability is
negligible — it is a four-character prefix on a user-chosen secret —
but it is not zero, and it is a silent authentication failure rather
than a loud one.

Applies only to secrets the cluster generates: exchange-minted keys,
the `_service_key_*` keys `get_api_token()` mints, and a new
"generate one for me" option on operator key creation. An
operator-supplied secret is whatever they chose and cannot carry our
prefix.

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
3. **Admin endpoints require a `cluster-admin` scope** (operator,
   2026-07-29). Closes scoped-key escalation inside `system`. Named
   `admin` when first implemented; renamed 2026-08-01 (operator)
   because it named no family and read as the admin family's
   wildcard. The same review confirmed both axes stay required, so a
   least-privilege administrative credential remains expressible.
4. **Full abuse resistance in v1** (operator, 2026-07-29). The
   endpoint is unauthenticated by nature and is the most exposed
   surface in the API.
5. **Exact and enumerated claim matching only.** No patterns in v1.
6. **Rules may share an issuer; mutating a rule does not affect
   already-minted keys.**
7. **Minted key names carry a random discriminator**, so re-runs never
   collide and never rotate a live key.
8. **The credential format moves here from phase 7** (operator,
   2026-07-29). Phase 3 mints the first cluster-generated keys, so the
   format has to exist before them or they need reissuing. The
   detection half — gitleaks in CI, the custom rule, the Loki query —
   is independent of the exchange and stays in phase 7.
9. **The `sfk_` prefix is reserved on operator-supplied secrets**, so
   that failing a bad checksum early at `/auth` is sound rather than a
   guess. Carries a documented upgrade caveat for the negligible but
   non-zero case of an existing secret already starting with `sfk_`.
10. **Family wildcards `<family>.*`** (operator, 2026-08-01).
    Granting a whole family is the common case in a mapping rule and
    enumerating three verbs invites getting one wrong. Matched on the
    family rather than on string prefixes, so `node.*` cannot reach
    `nodegroup.delete`.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | high | opus | none | Enforcement inversion, no scopes yet. Move `verify_token` onto `api_base.Resource.method_decorators` — but **not** `log_token_use`: measurement showed 3 of the 120 authenticated methods deliberately omit it, and `AuthNamespacesEndpoint.post` writes its own richer namespace-creation events instead, so moving it universally would double-log there and would not be the pure refactor this step is supposed to be. Leave it per-method; remove the 120 per-method `@api_base.verify_token` decorators; add an `@api_base.public` marker and apply it to exactly `Root.get`, `Livez.get`, `Readyz.get`, `AuthEndpoint.post`. Class-level decorators run outermost so auth precedes ownership checks — verify that ordering with a test, it is the load-bearing assumption. Add the structural test: enumerate `app.url_map` and assert every rule's methods either authenticate or are `@public`. Add a pre-commit check modelled on `tools/check-from-db-by-ref-namespace.sh` that fails if a resource method is added without either. No behaviour change intended: the full existing suite must pass unmodified. Commit subject: "api: authenticate every endpoint by default." |
| 3b | high | opus | none | Scope vocabulary and enforcement. Derivation (`<family>.<verb>` from resource class and method name, families defaulting from the class name with a `scope_family` class attribute override, verbs `read`/`write`/`delete`); the `@api_base.scope(verb=..., family=...)` annotation for overrides; enforcement on the same universal path added in 3a; wildcard `*` for tokens minted from unscoped keys; default-deny where derivation is impossible. Add the `cluster-admin` scope requirement to `caller_is_admin` per Decision 3 (delivered as `admin` and renamed on 2026-08-01; see that decision). Publish the vocabulary and derivation rule in the developer guide. Tests: derivation for each verb, override honoured, wildcard passes everything, scoped token denied outside its scopes, and an admin endpoint refused to a scoped `system` key. Commit subject: "auth: derive and enforce token scopes." |
| 3c | medium | opus | none | Cluster-minted credential format, absorbed from the old phase 7. A `shakenfist/util/credentials.py` (or similar) with `generate()` producing `sfk_` + 32 base62 random + 6 base62 CRC32 checksum, and `looks_valid(secret)` verifying prefix and checksum. Reserve the prefix: key create and update reject an operator-supplied secret starting with `sfk_`, mirroring the existing `_service_key` name reservation in `external_api/auth.py`. Wire generation into `get_api_token()`'s `_service_key_*` secrets and add a "generate one for me" option to operator key creation (a request with no `key` returns a generated one — the response already returns the key name, so returning the secret is an additive change). Early rejection at `/auth`: a presented secret carrying the prefix but failing the checksum is refused before any bcrypt comparison. Tests: round trip, checksum catches single-character corruption, prefix reserved at create and update, early rejection fires, an operator secret without the prefix is unaffected. Document the upgrade caveat (an existing secret beginning with `sfk_` must be rotated) in the operator guide. Commit subject: "auth: give cluster-minted key secrets a recognisable format." |
| 3d | medium | opus | none | `TrustedIssuer` object, following the `NamespaceKey` recipe exactly (`shakenfist/namespace_key.py`, `schema/namespace_key_data.py`, the `mariadb.py` three-layer accessors, `protos/database.proto`, `daemons/database/main.py` handlers, `OBJECT_NAMES_TO_CLASSES`). System-namespace-only CRUD endpoints under `/auth/issuers`. Unique on `name`. Run `tox -e genprotos`, never `grpc_tools` directly. Commit subject: "objects: add the TrustedIssuer object." |
| 3e | medium | opus | none | `MappingRule` object, same recipe, owned by its namespace, unique on `(namespace, name)`. CRUD under `/auth/namespaces/{namespace}/rules`, gated by `requires_namespace_ownership` (already defined in `external_api/auth.py` and used by key creation). Claim matcher validation at creation: exact strings or lists of strings only, at least one bound claim, referenced issuer must exist. Rules are deleted with their namespace. Commit subject: "objects: add the MappingRule object." |
| 3f | medium | opus | none | Identity token validation, no endpoint yet. A `shakenfist/federation.py` module: unverified header/claim peek to read `iss`; issuer lookup; `PyJWKClient` with `cache_jwk_set` and a configured `lifespan`; single-flight refetch on unknown `kid` (a lock per issuer, so concurrent requests collapse to one fetch); `aud`/`exp`/`nbf` verification; claim matching against a rule. Pure functions plus one cache object, no Flask. Tests use locally generated RSA keys and a mock JWKS endpoint — no network. Cover: good token, bad signature, wrong `aud`, expired, unknown `kid` refetch, refetch happens once under concurrency. Commit subject: "federation: validate identity tokens against trusted issuers." |
| 3g | high | opus | none | The exchange endpoint. `POST /auth/federated`, `@public`, body `{token, namespace, rule}`, implementing the eight-step order in the Design section. Mint via `NamespaceKey.new()`, with the secret produced by 3c's generator so every federated key is scanner-recognisable from the first one, and with the rule's scopes, `key_ttl` as expiry, provenance recording the rule reference and satisfied claims, and a random discriminator on the name. Audit events per the Design section, including the failed-exchange event against the rule's owning namespace and the deliberate silence when no owner can be identified. Response `{namespace, key_name, key}`. Config: `FEDERATION_MAX_TOKEN_BYTES`, `FEDERATION_JWKS_CACHE_SECONDS`. Commit subject: "auth: exchange identity tokens for scoped namespace keys." |
| 3h | medium | opus | none | Abuse resistance: the `(jti, rule_uuid)` replay table with a unique index (the failing insert *is* the detection — no read-then-write race), expiring at the inbound token's `exp` and reaped by the cluster daemon alongside expired keys; per-source rate limiting backed by MariaDB so the limit is cluster-wide rather than per worker, with `FEDERATION_RATE_LIMIT_PER_MINUTE` and `0` to disable. Tests: replay refused, same token against a second rule allowed, rate limit trips and recovers, reaper removes expired jti rows. Commit subject: "federation: replay and rate limit protection for the exchange." |
| 3i | medium | opus | none | Closeout. The trust-composition test from open question 11 (scoped key in A, trust B→A, read allowed across the boundary, write refused). The Authentik proof test: a mock issuer with `groups` claims, a rule binding them, a successful exchange with no code differing from the GitHub path. End-to-end functional coverage in `shakenfist/deploy/cluster_ci`. Docs: the scope vocabulary and derivation rule, the exchange flow, issuer and rule configuration, and a worked GitHub Actions example written against public GitHub concepts only — nothing about the private CI conductor. Glossary entries for trusted issuer, mapping rule and scope stop being future tense. Master plan open questions 1, 2, 3, 4, 5, 6, 9, 10, 11 marked resolved; phase 3 → Complete in the Execution table and `docs/plans/index.md`. Note the `sf-client federation ...` client-python follow-up in Future work, and confirm the master plan's phase 7 now reads as detection-only since its format half landed here. Commit subject: "docs: federated identity exchange." |

After each step the management session runs
`pre-commit run --all-files`, reads the diff against the brief, and
confirms no unrelated edits. After 3a additionally: the full existing
suite passed *unmodified*, since 3a is a pure refactor. After 3b: a
manual read confirming no endpoint lost enforcement. After 3g and 3h:
present the diff for operator review before commit — the exchange is
the security boundary of this whole plan. After 3i:
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
  **Mitigation:** the override list is one grep; 3i's documentation
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
  **Mitigation:** Decision 3. Additionally, 3e should consider
  warning at rule creation when the target namespace is `system`.
- **Risk:** reserving the `sfk_` prefix breaks an operator whose
  existing key secret already starts with it, and it breaks as a
  silent authentication failure rather than a loud one.
  **Mitigation:** the probability is negligible (a four-character
  prefix on a user-chosen secret) but not zero, so it is called out in
  the operator guide's upgrade notes rather than left to be
  discovered. 3c's brief makes documenting it part of the step.
- **Risk:** trust becomes a scope-escape hatch.
  **Mitigation:** open question 11's test, which is a named
  deliverable of 3i rather than an afterthought.

## Definition of done

- [x] Every registered route either authenticates or is one of a
      small, individually justified `@public` set, asserted by a test
      over `app.url_map` and backstopped by a pre-commit check.
- [x] Scopes derive automatically for all endpoints; overrides are
      explicit, greppable and documented; legacy unscoped keys carry
      the wildcard and behave exactly as before.
- [x] Admin endpoints require both the `system` namespace and the
      `cluster-admin` scope, and a `["cluster-admin", "node.read"]`
      credential can read a node but not delete one.
- [x] `TrustedIssuer` and `MappingRule` are database-backed objects
      with events, CRUD APIs, and the correct ownership gates; rules
      die with their namespace.
- [x] A GitHub Actions OIDC token can be exchanged for a scoped,
      expiring key that works with an unmodified `sf-client`.
- [x] The same machinery exchanges an Authentik-style token with
      configuration only, proven by test.
- [x] Replay of one token against one rule is refused; the same token
      against a different rule still works.
- [x] Scopes survive the trust boundary: readable across, not
      writable across, asserted by test.
- [x] No secret appears in any event; the failed-exchange event
      reaches the rule owner.
- [x] Every cluster-minted secret carries the `sfk_` prefix and a
      verifiable checksum; the prefix is reserved against
      operator-supplied secrets; a bad checksum is rejected before any
      bcrypt comparison; and the rotation caveat for a pre-existing
      `sfk_`-prefixed secret is in the operator guide.
- [x] `pre-commit run --all-files` clean; `tox -e genprotos` no-op;
      unit tests green. Re-confirmed by the pre-push audit after the
      rebase onto develop.
- [x] Functional CI green on the branch **before** the PR merges.
      Could not be established from a local tree and was the operator's
      gate at pull request time; #3625 merged as `c64269e63` on
      2026-08-06 with the merge queue green.
- [x] Master plan open questions 1, 2, 3, 4, 5, 6, 9, 10 and 11
      marked resolved; phase status updated in the Execution table
      and `docs/plans/index.md`; glossary and guides updated.
- [x] Pre-push security review findings triaged: every high fixed,
      every medium and low either fixed or recorded below with the
      reason it was accepted.

## Pre-push security review

The pre-push audit's security agent read the whole phase 3 diff. What
it found and what was done, kept here because the reasoning for an
accepted finding is worth more later than the finding itself.

Fixed, each with a regression test that was verified to fail when the
fix is reverted:

* **A rule could grant scopes its author did not hold.** A token scoped
  `rule.write` could write a rule granting `*`, satisfy that rule's own
  bound claims, and exchange it for a wildcard key — which in the
  system namespace reaches cluster-admin. `_rule_arguments` now applies
  the same ceiling `_namespace_keys_putpost` applies when minting a key
  directly.
* **Replay refusal was keyed on the token's signature text.** base64url
  leaves four don't-care bits in the final character of a 256 byte
  signature and the padding is optional, so one signature has many
  spellings that all verify — measured at 48 for an RS256 token. Each
  spelling was a separate replay slot. The identity is now derived from
  the signed material (header and payload), which the signature commits
  to and an attacker cannot vary.
* **The identity token was written to the API log.** `log_request`
  merges the request body into the decorated method's kwargs and logs
  them, redacting by field name — and the federated endpoint's
  credential field is `token`, which was not on the list. The body is
  now dropped entirely for any route under `/auth/`, matching what
  `app.py` already did for the audit event stream, with the predicate
  shared so the two cannot disagree.
* **The JWKS fetch used PyJWT's 30 second default timeout** while
  holding the issuer's refetch lock, so an unreachable provider could
  pin an API worker for 30 seconds per queued request. Now
  `FEDERATION_JWKS_FETCH_TIMEOUT_SECONDS`, default 5.
* **The exchange's body size check could not do its job.** It ran in
  the endpoint method, by which point `log_request` had already called
  `get_json(force=True)` and parsed the whole body; and it read
  `content_length`, which is `None` for chunked encoding, so a header
  choice opted out of the limit entirely. The refusal moved to a
  request hook ahead of every reader of the body, and a body with no
  declared length is now refused with 411 rather than measured.
* **Two trusted issuers could claim the same issuer URL**, making which
  provider's keys are trusted depend on listing order — an operator
  repointing an issuer would believe they had while some requests kept
  verifying against the old JWKS. Refused on create and update, using
  the same lookup token validation uses.
* **A mapping rule's `key_name_prefix` bypassed the reserved key name
  check.** A rule with the prefix `_service_key` minted keys colliding
  with the cluster's own service credentials, which is precisely what
  the key endpoints refuse. The reserved-name patterns moved to
  `util.credentials` so both paths ask the same question.
* **Rule fields had no upper bounds**, so an oversized value reached
  the database and returned a 500 instead of a message the operator
  could act on. `key_ttl` additionally had no ceiling, so a rule could
  mint a key outliving by a year the identity token that justified it.
* **A damaged rule row leaked its UUID to an anonymous caller.** The
  generic 500 handler answers with `repr(e)` and `CorruptMappingRule`
  names the rule; on the one endpoint anybody may call, that hands a
  stranger an identifier. Caught and turned into a generic refusal.
  The first attempt guarded the wrong call and protected nothing; see
  the pull request review below.

Assessed and accepted:

* **The rate limiter keys on `remote_addr` with no `ProxyFix`.** Behind
  a reverse proxy that does not rewrite the source address this is one
  global limit rather than a per-caller one. Trusting
  `X-Forwarded-For` unconditionally would be worse — it would let any
  caller choose their own rate limit bucket — and whether the header
  can be trusted is a property of a deployment we cannot see from here.
  Documented in both the config description and the operator guide
  instead.
* **The refusal reasons are coarse categories.** A caller learns
  "untrusted issuer" versus "token rejected" versus "no such rule".
  This is deliberate: the rule lookup happens after token validation,
  so an anonymous caller holding no valid token cannot use it to
  enumerate rules, and an operator debugging their own workflow needs
  to know which stage refused them. Which claim missed is still
  withheld.
* **`PyJWKClient` follows redirects when fetching a JWKS.** Reaching
  this requires the ability to register a trusted issuer, which is
  cluster-admin — an actor who already has total control. The `https://`
  requirement on `jwks_uri` stands.
* **The `federation_replay` and `federation_rate_limits` tables grow
  with traffic.** Both are already swept by the cluster daemon's
  reapers, so this was addressed before the review ran.

## Pull request review (#3625)

The automated reviewer on the pull request raised eight items. Three
were marked for fixing, five as things to consider. All eight were
addressed; the two that turned out to matter most are the first two,
because between them they show a mitigation and its own regression
test agreeing with each other and both being wrong.

Fixed:

* **The `CorruptMappingRule` guard was wrapped around a call that
  cannot raise it.** The exception comes from decoding `bound_claims`
  or `scopes`, which happens on the *attributes* read;
  `MappingRule.from_db_by_name` reads the static row and the object
  state and touches neither. The first read that could actually fail
  was the `issuer` comparison, outside the `try`, so the UUID leak the
  guard was written to prevent was still open. The endpoint now reads
  the whole policy once, inside a `try` that covers it, via the new
  `MappingRule.policy()`.
* **The regression test mocked the one function that cannot raise.**
  It patched `from_db_by_name`, so it passed against the broken guard
  — which is why the guard shipped in the wrong place. It now patches
  the attribute read, asserts the lookup really did succeed first, and
  was verified to fail (`401 != 500`) against the unguarded endpoint.
* **`arg_is_artifact_ref`'s docstring described the trust behaviour
  this branch removes**, telling the reader that ownership still lets a
  trusted namespace delete by UUID. It does not, as every other
  document in the branch says. Rewritten, and the trust case added to
  the `requires_artifact_access` comment, which had omitted it.

Considered, and changed:

* **Issuer resolution ran above the rate limit**, and the comment
  justified that ordering by calling the preceding steps free.
  `issuer_claiming_url` scans every configured issuer and reads state
  and attributes per row, so it was the one unauthenticated, unmetered
  database amplification path in the new code — and the ordering was
  backwards on its own logic, since the counter row it was avoiding
  costs less than the scan it was permitting. The meter moved above
  the lookup. Counting is one row per source per window, the same row
  a caller naming a real issuer already earned, so the table grows no
  faster. The test that asserted the old contract now asserts the new
  one.
* **`issuer_url` uniqueness was a check-then-write with nothing
  serialising it**, while three documents described it as an
  invariant. It cannot have a unique index, because a soft-deleted
  issuer keeps its row so its URL stays reusable, so both endpoints
  now hold a cluster lock across the check and the write — the
  `vsock_cids` pattern from `instance.py`. Tested by asserting the
  create happens between the acquire and the release.
* **Every attribute access re-read the row.** One exchange made five
  round trips for one rule. `policy()` reads once; the exchange and
  `external_view()` both use it. This fell out of the first fix.

Considered, and documented rather than changed:

* **Anonymous requests write an audit row before the rate limit
  applies.** `log_request_info` events every request to `API_REQUESTS`
  ahead of routing, so `_federated_refusal`'s care not to let an
  anonymous caller write unbounded rows into a *namespace's* log does
  not extend to the API's own. This is pre-existing and shared with
  `POST /auth`, the other public route, so it is out of scope for this
  branch — but the docstring no longer reads as though the exposure is
  closed.
* **Widened artifact name resolution does a trust lookup per
  candidate.** A popular name across many namespaces fans out to one
  trust read each. It only runs when the caller's own namespace has no
  match, so the common case is untouched. Recorded in the docstring as
  a deliberate choice, with the SQL pushdown that would fix it if it
  ever shows up in a profile.

## Second pull request review (#3625)

A re-review after the first round raised seven further items: two to
fix, one documentation correction, four to consider. All seven were
addressed. None of them contradicted a fix from the first round, and
the severity fell — the first round found a security hole with a
regression test that agreed with it, this one found one live
authorisation gap and otherwise inconsistencies between what the code
does and what this branch's own documents claim it does.

Fixed:

* **A trust still authorised destructive mutation of another
  namespace's artifact.** `ArtifactUploadEndpoint.post` and the cache
  route resolved a caller-supplied url with `Artifact.from_url`, whose
  predicate is *visibility* — so a trusted namespace could name the
  owner's `source_url`, land on the owner's artifact, and have its own
  blob added as the newest version. `add_index` ends in
  `delete_old_versions`, so the owner's older versions went with it.
  The root cause was one function serving both a read and a write
  intent, which is the same defect this phase already fixed for *name*
  resolution when it split `arg_is_artifact_ref` from
  `arg_is_visible_artifact_ref`. `from_url` now documents that it
  resolves by visibility, and the new `Artifact.owned_from_url()` is
  what a write path uses. Both routes then authorise the existing and
  the brand new cases apart: a trust is enough to gift a namespace an
  artifact it did not have, and not enough to replace what one it
  already owns resolves to. The audit event also moved below the check,
  so a refused caller can no longer append to the event log of a
  namespace it is about to be told does not exist.
* **Two documents still described the pre-reorder exchange sequence.**
  The developer guide and this plan both listed issuer resolution above
  the rate limit, and this plan contradicted itself within one file.
  Both lists now match the code, and the inline step comments in
  `AuthFederatedEndpoint.post` — which read 2, 3, 4-5, 7, 6, 8 — are
  renumbered, since an ordering argument that cannot be audited against
  the code is not much of an argument.
* **The operator guide overstated what a trust withholds.** It claimed
  a trusted namespace cannot change your objects' metadata, but
  `requires_namespace_ownership` *is* `namespace_is_trusted`, so a
  trusted namespace can add keys and write mapping rules in yours — the
  most privilege-bearing objects this phase adds. The behaviour matches
  the documented `add-key` precedent and is deliberate; the blanket
  claim was what needed narrowing. The section now says plainly that a
  trust is administrative access to a namespace's credentials, not only
  a window onto its resources.

Considered, and changed:

* **The rate limiter could fail open.** Both federation replies
  signalled failure by carrying a non-empty `error`, which left the
  fail-closed property resting on string formatting: an exception
  raised with no args has an empty `str()`, so the reply arrived as
  `attempts=0, error=''` and read as "nobody has tried this minute".
  Not reachable through any current path, because
  `_direct_count_federated_attempt` converts the plausible failures
  into `DatabaseUnavailable` with text — but an invariant carried by a
  string being non-empty is not one a future contributor will know to
  preserve. Both replies now carry an explicit `bool ok`, set only on
  the success path, and the client decides on that.
* **A damaged rule took the whole CRUD listing down.**
  `external_view()` called `policy()`, so one undecodable column turned
  a namespace's entire rule listing into a 500 and hid every healthy
  rule. Worse, `delete()` does the work and *then* builds the
  response, so a damaged rule was soft-deleted successfully and the
  caller still got a 500 — on the one operation that would have cleaned
  it up. `external_view()` now describes such a rule with an explicit
  `unusable` marker instead of raising. Note this is the opposite
  choice from the exchange, deliberately: the exchange must refuse,
  because bound claims it cannot read are bound claims it cannot check,
  while the CRUD routes exist to tell an owner which rule is broken.
* **`TrustedIssuer` was left with the read-per-property shape
  `MappingRule.policy()` was written to fix.** `validate_token` went
  back to the same row three times — `jwks_uri`, then `audience` and
  `issuer_url`. The new `TrustedIssuer.configuration()` reads once, and
  `JWKS_CACHE.signing_key()` takes the `jwks_uri` its caller is already
  holding rather than reading it again. Pinned by a test asserting the
  read count, because nothing else would notice a third read coming
  back. The `issuer_claiming_url` SQL pushdown the reviewer also
  suggested was *not* done: the scan is now below the meter, which was
  the security half, and pushing the match into SQL is a larger change
  than this branch should carry.
* **Reclaiming a rule name destroyed the superseded rule's events.**
  `hard_delete()` deletes object events, and on a rule those events are
  the refusal trail — a stream of near-miss claim failures is what
  probing looks like. The natural response to spotting it is to delete
  the rule and write a tighter one under the same name, which is this
  exact path, so acting on the evidence erased it. Both `MappingRule`
  and `TrustedIssuer` now record the supersession, with the superseded
  uuid, on the replacement object. Recorded there rather than on the
  namespace because `namespace.py` imports `mapping_rule`, and because
  the replacement is where somebody asking what happened to a rule will
  look. This preserves the fact and the identifier, not the trail
  itself; a fuller fix would copy the events, and the unique index
  means the old row still has to go.

Not done, and why:

* **A CI assertion for the artifact upload gap.** The reviewer
  suggested one in `test_trusts` alongside the delete assertions. The
  `shakenfist_client` package is not installed in the development
  environment, so `cache_artifact`'s signature could not be verified,
  and a functional test written against a guessed client API is worse
  than none. The unit coverage pins both halves of the fix instead, and
  each half was confirmed by reverting it and watching the specific
  tests fail.
* **A test that `reap_federation_records` is registered on the cluster
  daemon schedule.** True gap, but a uniform one: no test in the tree
  asserts schedule registration for any of the ten cluster tasks, and
  the block sits inside the elected loop where reaching it means
  refactoring `daemons/cluster/main.py`. Covering one task and not the
  nine beside it would read as assurance that is not there.
* **A test that a `key_name_prefix` collision does not rotate an
  existing key.** Investigated rather than accepted: the minted name
  carries an eight character random discriminator, so a collision is
  not reachable, and a test of that would be a test of probability.
  What *was* missing is the property the discriminator exists to
  protect — that the bare prefix is never used as a key name — so there
  is now a test that an operator's key named for the prefix still
  authenticates after a mint.

## Merge queue failure (#3625)

The first attempt to merge failed for two unrelated reasons. Three
matrix jobs died because the home lab pip mirror and proxy were
unreachable for about two minutes, which is infrastructure and not
this branch. The fourth found a real defect.

Every one of the sixteen tests in
`cluster_ci_tests/test_federation.py` failed in `setUp` with
`400 jwks_uri must be https`. `_start_jwks_server()` handed the issuer
an `http://` address, and `_validate_issuer_arguments` has refused
non-HTTPS `jwks_uri` since the `TrustedIssuer` object was added in
`6d9092944`. The test, written later in `0a6f5d651`, contradicted a
constraint that already existed. It went unnoticed because the
`(collection)` matrix is skipped on `pull_request` and only runs on
`merge_group`, so these tests had never executed.

The API is right and the test was wrong, so the test changed: the
throwaway JWKS server now speaks TLS behind a certificate it signs
itself, and the issuer is registered with an `https://` address. No
loopback or private-address exemption was added to the validator. A
JWKS fetched over plaintext can be substituted by anyone on the path,
and a rule with a hole in it for the convenience of tests is a rule
that will eventually be exercised in production.

The cost is stated rather than hidden. The cluster verifies against
the system trust store, so it refuses a self-signed certificate and
never reaches the handler; `_require_reachable_jwks` sees the JWKS was
never served and skips. Eleven tests — issuer and rule CRUD, ownership
gates, validation, malformed and oversized bodies, the unauthenticated
route — now run for real. The five that need a live callback skip with
a message naming the reason. Issue #3639 tracks giving CI a
certificate the cluster trusts, and records that reachability is
almost certainly not the obstacle, since the tests run on the primary
node alongside the API they call.

This is also the item the second review round raised and this plan
declined, on the grounds that `shakenfist_client` was not installed
locally so the call could not be verified. That was wrong: the client
installs from PyPI into a scratch venv without difficulty, and doing
so reproduces the failure in seconds. The lesson is recorded in
`AGENTS.md` under "Cluster CI tests only run in the merge queue".

## Third pull request review (#3625)

Eight items. Two fixed as defects, two as documentation, two as
smaller corrections, one deferred to an issue, one needing nothing.

**The damaged-rule handling did not work on a real cluster.** This
phase put considerable effort into a rule whose `bound_claims` or
`scopes` will not decode: the exchange refuses it with a generic 401,
and `external_view()` marks it `unusable` so one bad row does not take
a namespace's listing down. Neither happened on a deployed cluster.
The decode runs inside `sf-database`, the servicer's catch-all turned
`CorruptMappingRule` into `INTERNAL`, and
`_grpc_get_mapping_rule_attributes` converts any `RpcError` into
`DatabaseUnavailable` — so the API answered 503 and both behaviours
were unreachable. Every test for them patched `MappingRule._attributes`
in-process, which is the only path where they worked.

This is the mistake this plan already named under "A guard has to sit
where the exception is raised", committed one layer down: the guard
was in the right place, and the fault could not reach it. The reply
now carries `bool corrupt`, the servicer sets it instead of falling
into the catch-all, and the client re-raises `CorruptMappingRule`.
`str(e)` is logged rather than passed to `set_details()`, because the
message names the rule uuid and the exchange is unauthenticated. The
new tests drive the real servicer method and the real client wrapper
rather than a patched attribute; reverting either half fails exactly
the three that should.

**The replay key compared tokens as text.** `token_id` was
`sa.String(128)`, so it inherited the server's default collation —
`utf8mb4_general_ci` on 10.6, `utf8mb4_uca1400_ai_ci` from 11.4 — under
which two jti values differing only in case are one primary key, and
the second, legitimate, exchange is refused as a replay. It fails safe
rather than open, and the sha256 fallback is lowercase hex so it never
bites, but an issuer minting mixed-case base64 jti values is ordinary.

The obvious fix was wrong and testing caught it. `utf8mb4_bin` compares
case sensitively but is still PAD SPACE, so `'x'` and `'x '` remained
one key; the column is now `utf8mb4_nopad_bin`, which has existed since
MariaDB 10.2, comfortably below `MIN_MARIADB_VERSION` of 10.6. Because
no unit test can answer "does this server think these are the same
key", the tests are live ones behind `SF_MARIADB_TEST_DSN`, verified
against dockerised MariaDB 10.6 and 11.4. `tools/ci-enum-widening-test.sh`
now runs every `test_mariadb_*_live` module rather than one by name —
standing up MariaDB is the expensive part, and the script and its job
keep their old names so the required status check does not move.

**Two documentation gaps.** The release notes recorded that a trust no
longer authorises delete, share, unshare, retag or metadata changes,
but not that it no longer authorises adding a version to an artifact it
previously gifted — so tooling that re-uploads the same `source_url`
across a trust breaks with a 404 and nothing said so. The operator
guide's "Giving is a separate question from taking" read as though
re-gifting worked. Both now state where the line falls, and both note
that labels are unaffected because a label URL names its own namespace.

Also: the comment on `external_view()`'s `unusable` field opened with
"Only ever True", which is wrong — the field is False for every healthy
rule, as its own test asserts. Two documentation files were missing
trailing newlines; there is no `end-of-file-fixer` in
`.pre-commit-config.yaml`, which is why nothing caught them.

**Deferred.** Three call sites ending in `add_index` still resolve
URLs with the visibility-based `from_url` — instance create, the label
endpoint, and the artifact fetch operation. None is exploitable the way
the upload route was: the instance path fetches from the owner's own
URL rather than caller-supplied bytes, and label URLs are
namespace-scoped. But this phase writes the ownership rule down as
universal, so leaving three unremarked counter-examples would mislead.
`AGENTS.md` now names them and issue #3640 tracks narrowing them,
rather than growing this pull request further.

**No action.** The rate limiter keying on `remote_addr` without a
`ProxyFix` was reviewed and agreed with as already analysed and
documented. The absence of functional coverage for a successful
exchange is the subject of #3639, above.

## Follow-up: the deferred write paths (#3640)

#3625 merged as `c64269e63`. This section records what closing #3640
found, because two of the three sites turned out to be misdescribed in
the issue and the reasoning is worth more later than the diff.

**The label endpoint was the real hole, and the issue called it the
safe one.** #3640 recorded `LabelEndpoint.post` as "close to safe by
construction", because it resolves an `sf://label/<namespace>/<name>`
URL built from the request rather than taken from the caller. That
reading followed the construction and not the value: `_label_url`
accepts `<namespace>/<label>` and hands back the namespace it was
given. The route carries no ownership decorator, and the
`requires_admin=True` in its `swag_from` is prose appended to the
generated description. So any authenticated caller holding
`label.write` — which every legacy unscoped key does, through the
wildcard — could `POST /label/<somebody else>/<label>` and make its
own blob the newest version of their label, with
`delete_old_versions` taking the versions underneath. That is a wider
hole than the upload one #3625 closed, since it needed no trust and no
share.

It survived partly because the rest of the endpoint was broken.
`_label_url` returns a pair, and `get` and `delete` both handed the
whole pair to `url_filter`, which compares it against a string. Nothing
ever matched, and the resulting 404 was constructed but not returned,
so `get` fell through to an `IndexError` and `delete` to a `NameError`.
Both have answered 500 to every request since the pair was introduced
in 2024. An endpoint nobody could successfully read from is not one
anybody probed hard enough to find out what it would accept.

**The instance path is where the obvious narrowing was the wrong fix.**
Resolving `disk.base` with `owned_from_url` alone would have given every
namespace its own artifact for a shared image's URL — and
`transfer_image` treats an artifact with no versions as "cluster does
not have a copy", while `_http_get_inner` mints a fresh blob per fetch.
Every tenant would have downloaded and stored its own copy of every
shared image. The operator guide describes reuse as the entire point of
sharing one ("an official CentOS image that many users will want"), and
in the same paragraph says non-system namespaces "should not be able to
update such an artifact". Both halves of that sentence are the
requirement.

So the split is per verb rather than per artifact: a visible foreign
artifact is resolved to a blob and booted from, which is what the
label, snapshot and upload branches of the same loop already do, and
never fetched into. `owned_from_url()` picks the write target and
`from_url()` still picks what may be read. Two tests discriminate —
the enqueued fetch names a blob URL rather than the source URL, and its
`artifact_uuid` is not the foreign artifact's — and both fail when the
resolution is put back.

`Artifact.owned_from_url_or_new()` was added for the write paths whose
target namespace is fixed as the caller's own or already authorised.
They have no two cases to tell apart, so they get the create for free;
`owned_from_url()` itself still refuses to create, because the routes
which accept a caller-nominated namespace must authorise creating and
modifying separately.

**Behaviour changes worth flagging at review.** Updating a label across
a trust is now refused, which affects the `ci-images` pattern in the
operator guide: the first gift of a label works, a nightly republish
under the same name needs a key in the receiving namespace. This is the
same break #3625 already took for artifact uploads, and the guide
already said so for uploads — the label carve-out beside it was the
inconsistency, and it was wrong on the facts as well. Separately, a
tenant booting from a shared image's URL now pins to the blob that
image currently has rather than causing a refetch, which is the
intended reading of "should not be able to update".

## Back brief

Before executing any step of this phase, the implementing sub-agent
must back-brief the management session on its understanding of the
brief and surrounding context. The management session must present
the 3g and 3h diffs for operator review before commit — the exchange
endpoint is the security boundary this entire plan exists to create.

The 3g and 3h diffs were both presented. The operator elected to
review the phase as a whole at the pull request rather than
step by step, which is why both were committed before review.
