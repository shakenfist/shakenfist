# Authentication and Namespaces

Authentication in Shaken Fist has three moving parts, and most of this page is
an expansion of them.

A **namespace key** is a first-class object owned by a namespace, with its own
UUID, state machine and event stream. It may carry an expiry, a set of scopes,
and a record of where it came from.

An **access token** is a short lived JWT derived from a key by a request to the
REST API, and passed on subsequent calls as an HTTP header. It inherits the
key's scopes, and it is bound to the key by a nonce, so changing or removing
the key invalidates every token already derived from it. Tokens also expire on
their own, in which case a caller re-authenticates and retries.

A **scope** names a class of operation that a key, and any token derived from
it, is permitted to perform. A key with no scopes recorded may do anything its
namespace may do, which is how every key created before scopes existed
continues to behave.

Keys arrive two ways. An administrator creates one, or a workload that already
has an identity somewhere else -- a GitHub Actions job, a service account in an
Authentik instance -- exchanges that identity for one. A key obtained by
exchange is an ordinary namespace key in every respect; nothing downstream
knows or cares how it was created.

Terms used here are defined in the [glossary](../glossary.md).

Shaken Fist logically divides objects into "namespaces". These namespaces can be
thought of as tenants, although there might be other reasons to divide resources
into their own buckets -- for example the Shaken Fist CI system uses a namespace
to store an archive of the images used for CI runs, and that namespace is referred
to by the namespaces actually running tests. The process to create a namespace is
discussed in the *Creating namespaces* section below.

## Namespaces

All resources (instances, networks, network interfaces, and artifacts) are
assigned to a namespace. Notably, blobs are not within namespaces and more than
one artifact can refer to a given blob even if those artifacts are in different
namespaces. It is assumed that knowing the UUID of a given blob implies that
you can access it.

All requests to Shaken Fist have a namespace context. The namespace "system" is
reserved and is used for administrative actions. Please note that the
authentication configuration created by the deployer is for the system
namespace, and if used directly will result in instances and other objects
being created in that namespace. While this is supported and will function as
expected, it is probably undesirable for anything other than a single user
installation.

By default only requests in the system namespace are able to access resources
in other (foreign) namespaces. Before Shaken Fist v0.7 this behavior was hard
coded and not configurable. As of Shaken Fist v0.7, this is implemented in
the form of "trusts", where every namespace is configured to "trust" the system
namespace. This makes the resources visible to the system namespace. You cannot
remove the trust of the system namespace from your namespaces. However, you can
choose to trust additional namespaces, and this is done via the
`sf-client namespace trust ...` series of commands and associated API calls.

## Configuring a client

When the deploy playbook ran, it created two authentication artifacts on each
node which are useful to get started with Shaken Fist. First off, there
is `/etc/sf/sfrc`, which is a file you can source in your shell to provide
authentication environment variables. These environment variables can be used
by Shaken Fist command line clients, Ansible modules, and the Python API client
implementation itself. An example `sfrc` looks like this:

```
# Command line hinting
eval "$(_SF_CLIENT_COMPLETE=bash_source sf-client)"

# Client auth
export SHAKENFIST_NAMESPACE="system"
export SHAKENFIST_KEY="oisoSe7T"
export SHAKENFIST_API_URL="https://shakenfist/api"
```

The first line of the file enables tab completion for `sf-client` in a bash
shell. The last three lines are the important authentication details:

* the namespace we want to use is called "system".
* our access key is "oisoSe7T".
* the URL the API exists at is "https://shakenfist/api"

`sfrc` is only useful to users of Unix-like shells, so there is also a JSON form
of this configuration information, which is written by the deployer at
`/etc/sf/shakenfist.json`. Here's an example:

```
{
    "namespace": "system",
    "key": "oisoSe7T",
    "apiurl": "https://shakenfist/api"
}
```

The Shaken Fist command line clients, Ansible modules, and the Python API client
will look for configuration in the following locations:

* environment variables.
* `.shakenfist` in your home directory, that is `~/.shakenfist`.
* `/etc/sf/shakenfist.json`.

## Creating namespaces

You can create your first namespace like this, assuming you are authenticated
as the system namespace:

```
sf-client namespace create new-namespace
```

By default a new namespace has no access keys or trusts configured, and therefore
is only accessible to users of the system namespace.

## Namespace keys

Namespaces are accessed by providing a valid "key" for the namespace. While
keys have names, they do not have to be usernames and passwords -- my mental
model is more like API access tokens in something like GitHub than usernames and
passwords. I tend to create a new key for each program which is interacting with
the namespace, and then give it a descriptive name.

You can create a new key like this:

```
sf-client namespace add-key namespace-name keyname key
```

There can be more than one key for a namespace. The key name is not used as part
of the authentication process, and is largely used for key management (deleting
the key) and logging which access token was used in the event logs.

A key may also carry an expiry, and may have the cluster choose its secret
rather than supplying one; both are described below, and both are REST API
features which the command line does not expose yet.

???+ info

    Please note the key prefix "_service_key" is reserved for internal use within
    Shaken Fist. This usage is discussed in the *Inter-node authentication* section
    below.

### Keys are objects

A key is a first-class object owned by its namespace, with the same lifecycle
machinery as an instance or a network: its own UUID, state machine, event
stream, and soft delete. Practically, that means a key's history is auditable
-- when it was created, when it was rotated, when it expired -- rather than
being an anonymous entry in a JSON blob hanging off the namespace.

Nothing about this is visible on the wire. The key management endpoints and
their responses are unchanged, and a client written against an older release
behaves identically.

That backwards compatibility has a cost worth knowing about: the key listing
endpoint still returns a list of key *names*, so a key's expiry, scopes and
provenance are recorded on the object but are not readable through any API.
`NamespaceKey.external_view()` renders them and nothing serves it.

### Secrets the cluster generates

Creating a key without supplying a secret has the cluster generate one, which
is returned exactly once in the response. Generated secrets carry a recognisable
`sfk_` prefix and a checksum, which makes a leaked credential greppable and lets
a secret scanner reject lookalikes without calling the API. The prefix is
reserved against operator-supplied secrets so that `/auth` can reject a
malformed one before doing any bcrypt work.

The format, the reservation and the upgrade caveat are covered in the
[operator guide](/operator_guide/authentication/#cluster-generated-key-secrets).

### Rotation

Adding a key whose name already exists rotates that key rather than creating a
second one. The stored hash is replaced, and so is the nonce -- which means
every access token minted from the old secret stops validating immediately.
The key object itself survives with its UUID and its event history intact, so a
rotation shows up as an event on the key rather than as a delete and a create.

This is long-standing behaviour: `add-key` has always overwritten a key of the
same name. It is called out here because it is the mechanism behind revocation,
and because rotation clears any expiry the key previously carried.

### Expiry

A key may carry an optional expiry, as epoch seconds, supplied when the key is
created or updated. A key with no expiry never expires, which is the default
and matches every key created before this feature existed.

Enforcement is at the point of use, not on a timer. An expired key can neither
mint new tokens at `/auth` nor validate a request, from the instant it lapses
-- there is no window in which an expired key still works because a cleanup
task has not run yet. Tokens already minted from a key that later expires
remain valid until their own expiry, which is nominally fifteen minutes; delete
the key if you need them invalidated immediately, since that changes the nonce.

Expired keys are tidied up by the cluster daemon rather than vanishing the
moment they lapse. It soft-deletes keys that expired more than
`NAMESPACE_KEY_REAP_GRACE` seconds ago (one hour by default; set it to `0` to
disable reaping and keep expired keys forever), and the standard object reaper
hard-deletes them once they have been soft-deleted for `CLEANER_DELAY`. The
grace period exists so that an operator debugging automation which suddenly
stopped working can still see the key that lapsed. Because enforcement is at
the point of use, none of these timings affect security -- only how long the
evidence sticks around.

## Scopes

A token carries a set of **scopes** naming the classes of operation it
may perform. A scope is a `<family>.<verb>` string, and there are three
verbs:

| Verb | Meaning | HTTP methods |
|------|---------|--------------|
| `read` | Observe without changing | `GET`, `HEAD` |
| `write` | Create or modify | `POST`, `PUT`, `PATCH` |
| `delete` | Destroy | `DELETE` |

Scopes are not hand-assigned per endpoint. The verb comes from the HTTP
method and the family from the resource class, so coverage is
automatic and a newly added endpoint is governed the moment it exists.
The families in use are:

`admin`, `agentoperation`, `artifact`, `auth`, `blob`,
`clusteroperation`, `instance`, `interface`, `issuer`, `label`,
`network`, `node`, `rule`, `upload`

So `GET /blobs` needs `blob.read`, and `DELETE /instances/{ref}` needs
`instance.delete`. The unauthenticated endpoints (`/`, `/livez`,
`/readyz`) derive families of their own, but `@public`
short-circuits before enforcement so theirs are never consulted and
they are not part of the vocabulary an operator writes.

Everything under `/auth/namespaces/...` derives `auth`, including the
namespace's keys, mapping rules and capacity claims. That the claims
are a capacity concept rather than an auth one is a known wart: adding
a `claim` family would be a change to the vocabulary operators have
already written into mapping rules, so it has deliberately not been
made. `cluster-admin` is the gate that actually matters for them
either way.

Where the derivation would mislead, a resource sets `scope_family`, or
a method carries `@api_base.scope(...)` to override the verb. Both are
deliberate and greppable; `tools/check-endpoint-authentication.sh`
requires the decorator form to be outermost so it cannot be swallowed
by another decorator.

Two verbs exist only as overrides, because the HTTP method describes
the mechanism rather than the privilege:

| Verb | Endpoints | Why not the derived verb |
|------|-----------|--------------------------|
| `console` | `/instances/{ref}/vdiconsolehelper`, `/instances/{ref}/vdiconsoleproxy` | Both are `GET`, but both return credentials for interactive keyboard and mouse control of the guest. `instance.read` must mean observation, or a monitoring credential can take a machine over. |
| `execute` | `/instances/{ref}/agent/execute` | Arbitrary command execution inside a guest is a different privilege from creating an instance, and an operator would sensibly grant one without the other. |

Adding a verb is a vocabulary decision, not a convenience: the test is
whether anyone would sensibly write a mapping rule granting it alone.
The full set is pinned by a test over the real routing table.

### Wildcards and compatibility

A key with no scopes recorded mints a token carrying the wildcard `*`,
which satisfies everything. Every key created before scopes existed is
in that state, so nothing an operator already has behaves differently.
Tokens minted before the `scopes` claim existed carry no claim at all
and are likewise treated as wildcard, so an upgrade does not
invalidate tokens already in flight.

A scope of the form `<family>.*` grants every verb in one family, so
`blob.*` is `blob.read`, `blob.write` and `blob.delete` together.
Granting a whole family is the common case when writing a mapping
rule, and spelling out three verbs invites getting one wrong. The
match is on the family, not on characters: `node.*` does not reach
`nodegroup.read`.

Scoped keys are produced by the federated exchange. A scoped token is
refused with a 403 for anything outside its scopes, and is refused for
any endpoint whose scope cannot be derived — a scope system which
allows what it cannot classify is not one.

### Administrative endpoints

Endpoints guarded by `caller_is_admin` require **both** the `system`
namespace and the `cluster-admin` scope, in addition to the derived
scope for the operation itself. Being in the system namespace used to
be sufficient, which meant a narrowly scoped key minted into `system`
could reach every administrative endpoint. Unscoped keys carry the
wildcard and satisfy all of this, so existing administrative
automation is unaffected.

`cluster-admin` is hyphenated rather than dotted because it is not a
`<family>.<verb>` scope and does not name a family. Of the twenty
methods `caller_is_admin` guards, only two derive an `admin.*` scope;
the rest derive `node.*`, `issuer.*`, `auth.*` and `blob.read`. No
family wildcard can produce it, which is why it carries no dot.

Requiring both axes is deliberate. `cluster-admin` says the caller may
act administratively at all; the derived scope says which operation.
That is what makes a least-privilege administrative credential
possible:

```
scopes: ["cluster-admin", "node.read"]
```

grants cluster-wide visibility to a monitoring workload that provably
cannot delete a node. If administration were a single all-or-nothing
flag, that credential could not be expressed.

## Access tokens

A key is not sent on every request. It is exchanged once for a short lived
access token, and that token is what subsequent requests carry.

### Obtaining a token

The authentication endpoint `/auth` is used to obtain a token to authenticate
future API requests. For example, I can obtain an authentication token from the
REST API using `curl` like this:

```
curl -X POST https://shakenfist/api/auth -d '{"namespace": "system", "key": "oisoSe7T"}'
{
    "access_token": "eyJhbG...IkpXVCJ9.eyJmc...wwQ",
    "token_type": "Bearer",
    "expires_in": 900
}
```

That is, a HTTP POST request to the `/auth` endpoint for the REST API (in our
case hosted at `https://shakenfist/api`) with a JSON body containing a dictionary
of the namespace name and the key to use.

In the response the `access_token` value of  `eyJhbG...IkpXVCJ9.eyJmc...wwQ` is
our JWT token and has been truncated in this example for readability. Authentication
tokens expire after a fixed period of time (nominally 15 minutes), but you will
be informed that the token as expired by receiving a 401 Unauthorized response.
If that occurs, simply create a new token as above and retry your request.

Subsequent requests to the REST API pass the token via an `Authorization` HTTP
header, and should request a `Content-Type` of `application/json`. For example,
to list the namespaces in our deployment we would make a `curl` request like this:

```
curl -X GET https://shakenfist/api/auth/namespaces \
    -H 'Authorization: Bearer eyJhbG...IkpXVCJ9.eyJmc...wwQ' \
    -H 'Content-Type: application/json'
[
    {
        "name": "adhoc",
        "state": "created",
        "trust": {"full": ["system"]}
    }, {
        "name": "ci",
        "state": "created",
        "trust": {"full": ["system"]}
    }, {
        "name": "system",
        "state": "created",
        "trust": {"full": ["system"]}
    }
]
```

The JSON response here has been formatted for readability.

???+ info

    Note the word "Bearer" before the access token in the Authorization header.

### What is inside a token

JWT authentication tokens are base64 encoded parts separated by the `.` character.
They are therefore trivial to decode. A decoded example (generated by the online
decoder at https://jwt.io/) is:

```
{
    "alg": "HS256",
    "typ": "JWT"
}
.
{
    "fresh": false,
    "iat": 1669786988,
    "jti": "906f4bfa-3218-4d07-a036-ac6b44ded67e",
    "type": "access",
    "sub": [
        "system",
        "deploy"
    ],
    "nbf": 1669786988,
    "exp": 1669787888,
    "iss": "shakenfist",
    "nonce": "ByKNRUVBfMBoQC1Z"
}
.
HMACSHA256(
    base64UrlEncode(header) + "." +
    base64UrlEncode(payload),
    your-256-bit-secret
)
```

You can see here that Shaken Fist stores the authenticated namespace `system` and
the key used to authenticate `deploy` under the `sub` key in this token. *You should
not assume that the content of JWT tokens produced by Shaken Fist are opaque to
users.*

### The nonce

For releases prior to v0.7, the token was blindly trusted for authentication. From
v0.7 we verify that the named key still exists in the namespace before authorizing
API requests. This test is performed by updating a "nonce" value for a given key
when the key is updated. The JWT token a caller is handed includes this nonce, and
if the nonce we are handed on a request does not match the current value in the
database the request is rejected.

The nonce is therefore the revocation mechanism for derived tokens: rotating,
deleting or expiring a key changes or removes its nonce, and every token already
minted from that key stops validating on its next request.

## Federated identity

A workload that already has an identity somewhere else -- a GitHub
Actions job, a service account in an Authentik instance -- can trade
that identity for a scoped, expiring namespace key without anyone
having stored a long-lived Shaken Fist secret alongside it. The
long-lived CI secret is the thing this exists to delete.

Three objects make up the chain, and each is a real database-backed
object with its own events:

* A **trusted issuer** is an identity provider the cluster will
  believe. It is cluster-wide and administrative, because deciding who
  may vouch for identities here is not a per-namespace decision.
* A **mapping rule** is a namespace's standing offer: an identity from
  this issuer, carrying these claims, may mint a key here with these
  scopes. It is owned by the namespace it mints into.
* The **minted key** is an ordinary namespace key. Nothing downstream
  knows or cares that it was federated; it authenticates, carries
  scopes, expires and is reaped exactly as any other key is. Its
  provenance records the rule, the issuer, and the claims that were
  actually satisfied.

### The exchange

`POST /auth/federated` takes `{token, namespace, rule}` and returns a
key. The order it works in is a security property rather than an
implementation detail, because the endpoint is reachable by anyone:

1. Refuse if the body is larger than `FEDERATION_MAX_TOKEN_BYTES`,
   before anything parses it.
2. Count the attempt against the source address's rate limit.
3. Read the `iss` claim **without verifying anything** and refuse if no
   trusted issuer matches. No network yet.
4. Verify the signature against the issuer's JWKS.
5. Check `aud`, `exp` and `nbf`.
6. Load the named rule and check its bound claims.
7. Claim this `(token, rule)` pair, refusing a replay.
8. Mint the key.

Steps 1 to 3 preceding step 4 matter more than they look. The JWKS
fetch is a synchronous outbound HTTP request made inside a request
worker, so an unfiltered path would let anyone with a made-up `iss`
tie up workers on connections to a host of their choosing.

The meter is step 2 rather than step 3, and that is the ordering most
likely to be undone by accident. Only the argument checks sit above it.
Resolving the issuer is a scan of every configured issuer, reading
state and URL per row, so it is itself work worth metering -- with the
meter below it the cheapest request to send would be among the more
expensive ones to answer. A new step belongs below step 2 unless it
touches neither the database nor the network.

Signature verification is pinned to asymmetric algorithms. If `HS256`
were accepted, an attacker could sign a token using the issuer's
*public* key as the HMAC secret -- the public key being, by
definition, public.

### Claim matching

`bound_claims` values are exact strings, or lists of exact strings
meaning "any of these". There is no globbing, no regular expressions,
no prefix matching and no type coercion: a claim of `1` does not match
a matcher of `"1"`.

This is deliberate and is the most likely thing to feel restrictive.
Prefix matching on `repository` reads naturally right up until someone
registers `shakenfist-evil`, and the anchored patterns needed to make
it safe are exactly what reviewers get wrong. Enumerating the branches
and repositories you mean covers the realistic cases; if practice
proves otherwise, patterns can be added later with anchoring enforced
at rule creation. Shipping them as the default is the part that would
be hard to undo.

### Single use

An identity token is single-use *per rule*. Once exchanged through a
given rule it cannot be exchanged through that rule again -- but it
can still be exchanged through a *different* rule, which is how a
workflow that needs two namespaces gets into both.

The pair is claimed by inserting it into a table with a composite
primary key, so the failing insert *is* the detection and there is no
window in which two simultaneous presentations both succeed. The claim
is the last thing that happens before minting, so a refusal for any
other reason -- claims that do not match, a rule that has been deleted
-- leaves the token still usable.

### Configuring it

Configuring an issuer and writing a mapping rule are operator tasks, and
the worked end to end example -- registering GitHub as an issuer, writing
a rule, and the workflow YAML that exchanges a token -- lives with them in
the [operator guide](/operator_guide/authentication/#a-worked-github-actions-example).

## Inter-node authentication

Requests between Shaken Fist nodes use the same authentication system and REST API
as external API requests. When a node makes an API request to another node, the
originating node will create (or reuse) a "service key" specific to the namespace
of the original request.

When a request is made from the "system" namespace for a resource in a different
namespace, the API request is made using the foreign namespace and the foreign
namespace's service key.

Service keys exist in the namespace's key data structures just as other keys do,
and are therefore visible when you list keys. As of v0.7, service keys expire
after five minutes, and are never reused. Before v0.7 service keys were always
named "_service_key". From v0.7 service keys have a name of the form
"_service_key[a-zA-Z]+".

## Key storage

Shaken Fist stores keys in MariaDB across two tables: `namespace_keys` holds
the immutable values (UUID, owning namespace, key name), and
`namespace_key_attributes` holds what rotation changes (the hash, the nonce,
the expiry) along with the key's scopes and its provenance. The secret itself
is never stored -- what is kept is the base64 encoding of the secret after
salting and hashing, with the python `bcrypt` library performing salting,
hashing and verification.

The `(namespace, name)` pair carries a unique index, which is what makes a key
name unique within its namespace and what serves the per-namespace listing on
the authentication path.

Keys previously lived in a `keys` JSON column on the `namespace_attributes`
table. Existing keys are migrated into the new tables by
`sf-ctl ensure-mariadb-schema` during upgrade, preserving each key's hash,
nonce and expiry exactly, so tokens minted before the upgrade continue to
validate. The old column is left in place but is no longer read or written.
Note that this means downgrading after the migration loses any key created
afterwards; keys that predate the upgrade are unaffected.

## Secrets and the event log

Credentials never appear in events. This matters more than it might sound,
because events are written to syslog and shipped to Loki, so a credential in an
event is a credential in log aggregation -- somewhere with weaker access
control than the credential itself.

Concretely: minted tokens, presented tokens, stored hashes and nonces are all
absent from event payloads. What is recorded is the key *name*, which is what
makes an audit trail useful without making it a credential store.

The generic API request tracing, which records request and response bodies for
debugging, does not log bodies for any route under `/auth`. Those routes carry
plaintext key secrets inbound and minted tokens outbound. The request URL is
still recorded, so the trace retains the namespace and the key name and loses
only the credential.

### Secret-carrying fields are typed, not just handled carefully

Every credential leak found so far had the same shape -- `extra={'token':
token}`, or an f-string, with the logging or event layer coercing the value to a
string on the way out. Nothing in the type system objected, and review caught
them one at a time, repeatedly. So the types object now.

Fields which carry a credential are `pydantic.SecretStr`. `str()`, `repr()`,
f-strings and `%s` formatting of one all yield `**********`, and the real value
comes back only from an explicit `.get_secret_value()` call. That call is the
point of the design: each one is somewhere a reviewer can stop and ask whether
the plaintext belongs there, and there are few enough to read in one sitting.

The wrapped fields are `NamespaceKeyAttributesData.key` and `.nonce`, and the
configuration options `AUTH_SECRET_SEED`, `MARIADB_PASSWORD` and
`LOKI_AUTH_HEADER`. The unwrap sites are the bcrypt comparison in `/auth`, the
nonce comparison in `verify_token()`, the JWT claim in `create_token()`, the two
SQL writes and the gRPC encoder for key attributes, the JWT signing key in
`external_api/app.py`, the database connection URL, and the Loki push header.

Three consequences are worth knowing before touching this code.

**`SecretStr` never compares equal to a `str`.** `SecretStr('x') == 'x'` is
`False`, so a comparison against a bare string literal is silently always false
rather than a type error. `config.py` keeps the value it compares
`AUTH_SECRET_SEED` against as a named constant,
`UNCONFIGURED_AUTH_SECRET_SEED`, and unwraps for the comparison: getting this
wrong makes the "you must configure a seed" refusal unreachable and lets a
cluster sign every token in its zone with the default shipped in the source.

**Assertions about secrets must compare `.get_secret_value()`.** Both obvious
alternatives are broken, and neither fails loudly.
`assertNotIn(attrs.key, haystack)` *cannot* fail: `SecretStr` implements no
`__contains__`, so the containment raises `TypeError`, and `testtools`'
`Contains` matcher catches `TypeError` and reports "does not contain".
`assertNotIn(str(attrs.key), haystack)` asserts that the literal `**********`
is absent, which is true of an event which leaked the real secret. Either
spelling turns a leak guard into a test which passes while checking nothing.

Wrapping the key fields did exactly that to six existing guards across three
files, and not one of them failed to announce it — every one was found by going
looking. So `ShakenFistTestCase` now raises `TypeError` when either operand of
`assertIn`/`assertNotIn` is a `SecretStr`, which makes the shape impossible to
write silently. `test_testcase_secret_guard.py` pins that check, including that
it leaves ordinary assertions alone, and `test_namespace_key_object.py` routes
its own guards through a single unwrapping helper whose ability to fail is
itself tested.

**The schema generator needs an explicit column mapping.** `SecretStr` maps to
`VARCHAR(255)` in `PYTHON_TO_SQLALCHEMY` (`shakenfist/schema/sqlalchemy.py`),
identically to `str`, so wrapping an existing field needs no migration. Without
that entry the generator's unknown-type fallback only logs a warning and returns
`LONGTEXT` -- and because tables are created from the model only when they are
absent, that would diverge fresh installs from every upgraded cluster with no
schema version change to notice it.

Type safety does not replace the structural protections around it. The `/auth`
body redaction covers a credential which arrives before any model exists, and
the two places which dump every configuration option — the `sf-queues` startup
banner and `_config_failure()` — redact by configuration *key name*, because
they iterate every option including ones which do not carry a `SecretStr` yet.
Both route through `config.redacted_config_items()` so they cannot disagree
about which names are secret. Each mechanism covers a gap the others do not, so
do not remove one as redundant.

Finally, a serialised view is not a safe home for a credential.
`external_view()` on both `NamespaceKey` and `BlobTransfer` deliberately omits
the secret material, because those views are passed directly into events and
log fields.
