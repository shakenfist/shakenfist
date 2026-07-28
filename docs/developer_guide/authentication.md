# Authentication and Namespaces

Shaken Fist uses JWT tokens for authentication and access control. These tokens
are created with a request to the REST API and then passed as part of subsequent
calls in the form of a HTTP header on the request. The tokens can expire, in
which case a caller needs to re-authenticate and then retry their request. The
process to create and use a token is discussed further in the
*Authentication* section below.

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

## Authentication

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

## Key management

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

???+ info

    Please note the key prefix "_service_key" is reserved for internal use within
    Shaken Fist. This usage is discussed in the *Inter-node Authentication* section
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

## Authenticating directly to the REST API

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

## Contents of the JWT tokens

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

For releases prior to v0.7, the token was blindly trusted for authentication. From
v0.7 we verify that the named key still exists in the namespace before authorizing
API requests. This test is performed by updating a "nonce" value for a given key
when the key is updated. The JWT token a caller is handed includes this nonce, and
if the nonce we are handed on a request does not match the current value in the
database the request is rejected.

## Inter-node Authentication

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

## Key Storage

Shaken Fist stores keys in MariaDB across two tables: `namespace_keys` holds
the immutable values (UUID, owning namespace, key name), and
`namespace_key_attributes` holds what rotation changes (the hash, the nonce,
the expiry). The secret itself is never stored -- what is kept is the base64
encoding of the secret after salting and hashing, with the python `bcrypt`
library performing salting, hashing and verification.

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