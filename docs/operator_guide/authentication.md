# Authentication

???+ info

    For a detailed discussion of how Shaken Fist authentication works, please see
    the discussion in the [developer guide](/developer_guide/authentication/).

Terms used here are defined in the [glossary](../glossary.md).

## Key expiry and reaping

Namespace keys may carry an optional expiry. Expiry is enforced when the key is
used, so an expired key stops authenticating the instant it lapses -- there is
no window during which it still works because a cleanup task has not run yet.

Tidying up expired keys is a separate, purely cosmetic concern. The cluster
daemon sweeps every fifteen minutes and soft-deletes keys which expired more
than `NAMESPACE_KEY_REAP_GRACE` seconds ago; the standard object reaper then
hard-deletes them once they have been soft-deleted for `CLEANER_DELAY`. A key
whose static row has no state row at all (a "zombie", see the orphan
reconciliation section of the [database guide](database.md)) is skipped by
this sweep — the hourly orphan reconciliation repairs it, after which the
normal reap path removes it.

| Setting | Default | Effect |
|---------|---------|--------|
| `NAMESPACE_KEY_REAP_GRACE` | `3600` | Seconds after expiry before a key is soft-deleted. `0` disables reaping, retaining expired keys forever. |

The grace period is there so that an operator investigating automation which
suddenly stopped working can still see the key that lapsed. Lengthen it if you
want a longer forensic window; set it to `0` if you would rather expired keys
were never removed. Neither choice affects security, because enforcement does
not depend on the sweep having run.

## Cluster generated key secrets

Secrets Shaken Fist generates itself — the short-lived service keys
used between nodes, keys minted by the federated exchange, and any key
you ask the cluster to generate for you — carry a recognisable format:

```
sfk_<32 random characters><6 character checksum>
```

The prefix makes a leaked credential greppable in logs and
repositories; the checksum lets a secret scanner reject lookalikes
without calling the API. It costs nothing cryptographically, because a
bearer credential is a random identifier rather than ciphertext, so the
prefix is a label beside the random part rather than a revealed piece
of it.

To have the cluster generate a key for you, create the key without
supplying a secret. The generated secret is returned exactly once —
only its bcrypt hash is stored, so it cannot be recovered afterwards.

### The prefix is reserved

`sfk_` may not be used at the start of an operator-supplied key secret;
attempting it is refused with a 400. This is not cosmetic. `/auth`
rejects a presented secret which carries the prefix but fails its
checksum *before* bcrypt comparing it against anything, and that
shortcut is only sound if no legitimate operator secret can be shaped
that way.

!!! warning "Upgrade note"

    If you have an existing key whose **secret** happens to begin with
    `sfk_`, it will stop authenticating after this upgrade, and it will
    fail as an ordinary 401 rather than with a distinctive error. A
    four-character prefix on a secret somebody chose makes this very
    unlikely, but it is not impossible. If an automation begins failing
    to authenticate immediately after upgrading and you cannot explain
    it, check whether its key starts with `sfk_` and rotate it.

    Key *names* are unaffected — only secrets.

## Keys and the event log

Credentials are deliberately absent from Shaken Fist's events. This is worth
knowing about because events are written to syslog *and* shipped to Loki, so
anything in an event has left the cluster and is sitting in log aggregation,
which usually has weaker access control than the credential does.

Events record the key *name* that was used, never the key secret, its stored
hash, its nonce, or any minted or presented token. Separately, the API request
tracing that records request and response bodies skips bodies entirely for
routes under `/auth`, since those carry plaintext key secrets inbound and
minted tokens outbound. The request URL is still logged, so you keep the
namespace and the key name.

If you have log tooling that greps for tokens in Shaken Fist events, it will
find nothing from these releases onward. That is the intent, not a regression.

## Upgrading

Keys used to live in a JSON column on the `namespace_attributes` table and are
now first-class objects in their own tables. Migration happens during
`sf-ctl ensure-mariadb-schema`, preserving each key's hash, nonce and expiry
exactly, so tokens minted before the upgrade keep validating and no operator
action is required.

The migration is one-way in the usual sense: keys created *after* the upgrade
are not written back to the old JSON column, so downgrading to a release that
predates this change loses them. Keys that existed before the upgrade are
unaffected either way. The exposure is one upgrade cycle, and it matches the
precedent set by the `node_daemon_states` migration.

## Federated identity

Workloads with an identity elsewhere -- a GitHub Actions job, a service
account in an Authentik instance -- can trade that identity for a
scoped, expiring namespace key rather than holding a long-lived Shaken
Fist secret. The mechanics are in the
[developer guide](/developer_guide/authentication/#federated-identity);
this covers what an operator has to decide and configure.

### Trusting an issuer is an administrative act

A **trusted issuer** is cluster-wide, and creating one is a `system`
namespace operation. It says the cluster will believe tokens this
provider signs, so the decision belongs with whoever is responsible
for the cluster rather than with an individual namespace owner.

Four fields, all mandatory:

| Field | Notes |
|-------|-------|
| `name` | How rules refer to this issuer. Treat it as durable |
| `issuer_url` | Compared to a token's `iss` claim **exactly**: no normalisation, no trailing-slash tolerance |
| `jwks_uri` | Where the signing keys are published. Always taken from here, never from the token |
| `audience` | Tokens must be minted for this. Usually your cluster's API URL |

The exact `issuer_url` comparison is deliberate. A loose comparison
here is a way to accept tokens from somewhere else entirely, so if
your tokens are refused with an untrusted-issuer message, check for a
trailing slash before checking anything else.

???+ warning "Rules reference issuers by name"

    Deleting an issuer does not delete the mapping rules that name it;
    those rules simply stop working. Recreating an issuer under the
    same name silently rebinds every rule that named it, potentially
    to a different identity provider than the rules' authors intended.
    Renaming rather than recreating avoids this.

### Delegating to namespace owners

A **mapping rule** is owned by the namespace it mints into, and
creating one requires ownership of that namespace -- the same gate as
adding a key, because a rule is the same privilege granted in advance
and conditioned on claims. Once you have configured an issuer,
namespace owners can write their own rules without coming back to you.

Two things to watch:

* **Rules targeting `system`.** A rule that mints into the `system`
  namespace is a standing offer of administrative credentials to
  whoever satisfies its claims. This is permitted, because there are
  legitimate uses, but the cluster logs a warning and writes an audit
  event when such a rule is created. Those events are worth alerting
  on.
* **Scope breadth.** A rule grants exactly the scopes it lists.
  `artifact.*` is a whole family; `["cluster-admin", "node.read"]` is
  a genuinely least-privilege monitoring credential. Listing a rule's
  scopes is how you audit what a federated workload can do.

Listing a namespace's rules answers "who can get into this namespace",
which is the inbound counterpart to listing its trusts.

### Abuse resistance

`/auth/federated` is unauthenticated by nature, so it carries its own
protections.

**Replay.** An identity token is single-use per rule. The same token
may still be exchanged through a *different* rule, so a workflow can
reach two namespaces with one identity. Seen pairs live in the
`federation_replay` table until the token they describe expires, and
are reaped by the cluster daemon.

**Rate limiting.** Attempts are counted per source address per minute
in the database, so the limit is cluster-wide rather than per API
worker. Note that behind a reverse proxy which does not rewrite the
source address, every request appears to come from the proxy and the
limit becomes a single global one -- size it accordingly, or disable
it and rate limit at the proxy instead.

Both checks fail closed: if the database cannot be reached the
exchange answers 503 rather than assuming the request is fine.

### Settings

| Setting | Default | Notes |
|---------|---------|-------|
| `FEDERATION_JWKS_CACHE_SECONDS` | 300 | How long an issuer's published keys are cached. Lower shortens the window in which a revoked key is still accepted; higher reduces load on the provider. An unknown key id always triggers an immediate refetch, so raising this does not delay recognising a rotated key |
| `FEDERATION_MAX_TOKEN_BYTES` | 16384 | Largest exchange request accepted, refused before parsing. A real identity token is one to two kilobytes |
| `FEDERATION_RATE_LIMIT_PER_MINUTE` | 60 | Exchange attempts allowed per source address per minute. `0` disables rate limiting entirely |

### If nobody uses it

Federation is inert until an issuer exists. A cluster which never
creates one behaves exactly as it did before: the two tables stay
empty, the reaper does nothing, and `/auth/federated` refuses
everything with an untrusted-issuer message.

## Trusts

???+ info

    Trusts are a newer way of sharing between namespaces with granular control.
    If you instead are interested in making artifacts available to all users of
    a Shaken Fist cluster, then you should also consider artifact sharing, which
    is discussed in the [artifacts section of the operators guide](artifacts.md).

The system namespace is special in a Shaken Fist cluster in that it can see
objects in all other namespaces. That is, if you are authenticated as the system
namespace and list instances, you get not only the instances in the system
namespace, but also all those in other namespaces. The same is true for other
namespaced objects such as networks and artifacts.

In older versions of Shaken Fist this behavior was hard coded, but as of
Shaken Fist v0.7 this is now implemented more flexibly. The system namespace
must still be able to see every other namespace, but you can also create a
"trust" relationship between two arbitrary namespaces to achieve the same result
on a smaller scale. In fact, the system namespace is now simply a default trust
that all other namespaces have a relationship with.

The Shaken Fist CI system uses these trusts for base images for CI runs. Each
night we rebuild a series of base test images -- Debian 10, Debian 11, Ubuntu
20.04 and so on. Each Shaken Fist CI job is run in its own namespace, so we needed
a place to store these base images, as well as a mechanism for other CI jobs to
be able to see them.

What we implemented was:

* a namespace to store the base images (we called it `ci-images`).
* when our CI conductor creates a new CI runner and associated namespace, it
  creates a trust between that ephemeral namespace and the `ci-images` namespace.
* jobs to create new images build them in their local namespace, and then "gift"
  them to the `ci-images` namespace via a label.
* jobs which need to boot a test image can now see the images from the `ci-images`
  namespace by virtue of this trust relationship.