# Security model


- Multi-tenant with namespace isolation
- JWT-based authentication, minted from namespace keys and bound to the
  minting key's nonce so that rotating or deleting a key revokes its
  outstanding tokens immediately
- Namespace keys are database-backed objects with optional expiry, enforced
  when the key is used rather than by a sweep
- Credentials never enter events, which are shipped to syslog and Loki;
  events record the key name, and neither of the two request loggers records
  a body for routes under `/auth`. Both consult one predicate,
  `api_base.handles_credentials()`, and drop the body wholesale rather than
  redacting named fields — a name-based rule leaks the day a route arrives
  whose credential field it has not heard of
- Secret-carrying fields are `pydantic.SecretStr`, so stringifying one
  anywhere yields `**********` and the plaintext comes back only from an
  explicit `.get_secret_value()` call. This covers the namespace key hash
  and nonce plus `AUTH_SECRET_SEED`, `MARIADB_PASSWORD` and
  `LOKI_AUTH_HEADER`, and complements rather than replaces the structural
  rules above: the request-body rule catches a credential arriving before
  any model exists, and the `sf-queues` startup banner redacts by
  configuration key name because it iterates every option, including ones
  not yet typed. Both leaks closed during this work — that banner, and
  `BlobTransfer.external_view()` publishing a transfer's authorisation
  token into events — were found by querying Loki for the credential
  rather than by review. See
  [`authentication.md`](authentication.md), which also explains why
  assertions about secrets must compare `.get_secret_value()`: both
  obvious alternatives make a leak guard pass while checking nothing
- RBAC with admin/user roles
- Network isolation via VXLAN

## Object visibility and the two artifact guards

Namespace isolation is enforced at two different granularities, and the
artifact endpoints are where both are visible.

Listing endpoints filter in the query or in a filter callable —
`namespace_or_shared_filter(namespace, obj)` for artifacts, which admits the
caller's own namespace, any namespace whose trust list names the caller,
`system`, and anything flagged `shared`.

Single-object endpoints cannot filter, because they resolve the object before
they know who is asking. `arg_is_artifact_ref` short-circuits a UUID straight
to `Artifact.from_db` with no namespace filter at all — deliberately, since
system callers legitimately reach across namespaces — so the whole of the
authorization decision rests on the decorator that runs next. There are two:

- `requires_artifact_access` guards the read-only routes (the artifact, its
  events, versions and cluster operations) and calls
  `namespace_or_shared_filter`, the same predicate the listing uses. That
  reuse is deliberate: "appears in the list" and "is readable by UUID" have to
  be one rule, and for as long as they were two copies of a rule they
  disagreed.
- `requires_artifact_ownership` guards everything that mutates, and tests
  `request_namespace() not in [a.namespace, 'system']` — the caller's own
  namespace, or the cluster admin. It consults neither the `shared` flag nor
  the trust list. Sharing publishes an artifact for reading rather than
  transferring it, and a trust is a visibility grant: the operator guide
  introduces it as the system namespace's cross-namespace *sight* on a
  smaller scale, and being able to delete somebody's artifacts is not a
  smaller-scale version of being able to see them. This matches
  `requires_instance_ownership` and `requires_network_ownership`, which have
  always read exactly this way; artifacts were the one object type where
  trust reached past reading.

Creating an object *in* a namespace which trusts you is a different question
and remains allowed — see the `namespace_is_trusted` checks on the artifact
cache and upload routes, and on instance creation. That is the "gifting"
pattern the operator guide's `ci-images` example is built on. It is additive,
the receiving namespace opted in by extending the trust, and nothing it
already had is lost.

Both refuse with `404` rather than `403`, so a refusal does not confirm that
the object exists. Scope enforcement is a separate and earlier gate — a caller
who fails the scope check gets `403` without either decorator running.

### Resolving a name

Guarding a lookup is a separate problem from performing one, and for a name
the two have to agree. `{artifact_ref}` accepts a UUID or a name; a UUID
identifies one artifact, but names are unique only within a namespace, so
resolution needs a scope and the obvious scope — the caller's own namespace —
is narrower than what the caller can see. That gap is visible from outside: a
tenant reads a shared image's name out of `GET /artifacts` and then gets a
`404` asking for it by that name.

`Artifact.from_db_by_ref_visible_to(ref, requestor)` closes it in two phases,
and the ordering carries more weight than the widening:

1. `from_db_by_ref(ref, requestor)`, unchanged, including raising
   `MultipleObjects` when the name is ambiguous inside the caller's own
   namespace. Whatever your own namespace resolves to wins. Without this,
   sharing an artifact called `debian-11` would silently retarget every
   tenant who already had one of their own by that name.
2. Only on a miss, an unscoped query by name filtered through
   `namespace_or_shared_filter` — the same predicate the listing and the read
   guard use. More than one survivor raises `MultipleObjects`, because a
   tenant cannot disambiguate with the `namespace` body field (they may only
   name their own) and picking one would be a guess. The error points at the
   UUID, which is never ambiguous.

Phase one is the fast path and stays free of phase two's per-candidate trust
lookups.

Widening applies to reading only, and the split is expressed as two decorators
over one shared body (`_resolve_artifact_ref`) so the pairing is visible at
each route:

| Decorator | Pairs with | A name resolves to |
|-----------|------------|--------------------|
| `arg_is_visible_artifact_ref` | `requires_artifact_access` | anything you can see, own namespace first |
| `arg_is_artifact_ref` | `requires_artifact_ownership` | your own namespace only |

The split follows the authorization guard, not the HTTP verb — `GET
/artifacts/{ref}/metadata` is ownership-guarded and therefore resolves
narrowly. Either decorator also drops back to the narrow behaviour when the
caller named a namespace in the request body, since that caller asked about
one namespace specifically.

Since `requires_artifact_ownership` no longer honours trust, narrow resolution
mostly agrees with the guard that follows it, and the split is defence in
depth rather than the only thing standing between a name and somebody else's
artifact. It still earns its place twice over. It keeps resolution from
silently following if the guard is ever widened again, and it is observable:
faced with a name matching two artifacts the caller can see but does not own,
the read route must answer `400` and ask which one, while a write route
answers a flat `404` rather than confirming that two exist.

Instances and networks have the same shape in `arg_is_instance_ref` and
`arg_is_network_ref` and have not been widened. Sharing is an artifact-only
concept by design, so only the trust half would apply to them.

### Resolving a url

The same read/write split applies to URLs, and that is the one that went
unnoticed for longer. `Artifact.from_url` filters by
`namespace_or_shared_filter`, so it can return an artifact belonging to
whoever shares with or trusts the caller. Right for a caller which will read
the result; wrong for one which will write to it, because the write is
`add_index` and `add_index` ends in `delete_old_versions`.

| Resolver | Answers | Predicate | Creates |
|----------|---------|-----------|---------|
| `from_url()` | what may I read | visibility | optionally |
| `owned_from_url()` | what may I write to | ownership | never |
| `owned_from_url_or_new()` | as above, target namespace already settled | ownership | yes |

`owned_from_url()` deliberately does not create, because a route which accepts
a caller-nominated namespace has two cases to authorise apart: a trust is
enough to gift a namespace an artifact it did not have, and not enough to
replace what one it already owns resolves to. `owned_from_url_or_new()` exists
for the callers which have no such split — their target namespace is their own
or has already been checked — so they need not restate it. The artifact fetch
and upload routes are the ones which do have the split, and spell it out
deliberately; the resemblance between them is the authorisation, not a
duplication waiting to be factored out.

`owned_from_url()` also passes the namespace to SQL as a query criterion, not
only to the Python predicate. Ownership is a plain equality, and this runs on
the instance create path. The predicate is still there, and has to be: the
object iterator drops a namespace criterion of `system` so that listing as
system sees the whole cluster, so a pushdown standing on its own would turn an
ownership test into no test at all. Visibility cannot be pushed down the same
way — it is a trust graph walk, and narrowing the query to one namespace would
drop the shared and trusted rows `from_url()` exists to find.

Instance creation from a plain URL is where the two verbs meet, and it is
worth knowing why it is not simply the ownership call. Resolving `disk.base`
by ownership alone would give every namespace its own artifact for a shared
image's URL, and `transfer_image` treats an artifact with no versions as
"cluster does not have a copy" — so each namespace would download and store
its own copy of every shared image, which is the opposite of what sharing one
is for. A visible foreign artifact is therefore resolved to a blob and booted
from, exactly as the label, snapshot and upload branches of the same loop
already do, and never fetched into. Read theirs, write only your own.

`Instance.snapshot()` is the fourth write path, and was missed by the original
sweep because that only covered `external_api/` and `operations/`. It is not
reachable across namespaces — the URL carries the instance UUID and the type
filter pins it to `TYPE_SNAPSHOT` — but it resolves by ownership anyway, so
the next artifact type minted against an instance URL does not have to
rediscover the rule. When looking for write paths, grep for the sink
(`add_index`) rather than for callers of the resolver.

## VDI console token trust model

The Kerbside VDI console proxy integration uses **offline signature
verification**. Shaken Fist is the sole signer: `sf-api` mints short lived
Ed25519 (`EdDSA`) JWTs describing the instance, namespace, audience, expiry,
and a single-use `jti`. The Kerbside proxy is a pure verifier — it holds only
the public key (fetched from `GET /admin/vditokenpubkey`) and never any
private material, so a compromised proxy cannot mint valid tokens. There is
no callback to `sf-api` on the connection hot path.

The private signing key lives in a single `cluster_config` row,
`KERBSIDE_JWT_SIGNING_KEY`, with custody parallel to `AUTH_SECRET_SEED`. The
row holds a newest-first, two-key window of Ed25519 keypairs; rotation
(`sf-ctl rotate-kerbside-signing-key`) prepends a fresh key and trims to two,
so tokens signed by the previous key stay verifiable until the next rotation.
`shakenfist/util/vdi_tokens.py` is the only module that parses the row.
Per-node `spice_server_cert_subject` (published by `shakenfist/node.py`) is
consumed by Kerbside as the enforced backend `host_subject`. See
`docs/operator_guide/vdi_console_tokens.md` for the operator runbook.
