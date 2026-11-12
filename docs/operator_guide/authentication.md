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
this sweep — the hourly orphan reconciliation repairs it. A zombie key which
is still live is repaired to `created`, never deleted, after which the expiry
sweep governs it normally. Re-running `sf-ctl bootstrap-system-key` for an
existing key also heals a missing state row.

| Setting | Default | Effect |
|---------|---------|--------|
| `NAMESPACE_KEY_REAP_GRACE` | `3600` | Seconds after expiry before a key is soft-deleted. `0` disables reaping, retaining expired keys forever. |

The grace period is there so that an operator investigating automation which
suddenly stopped working can still see the key that lapsed. Lengthen it if you
want a longer forensic window; set it to `0` if you would rather expired keys
were never removed. Neither choice affects security, because enforcement does
not depend on the sweep having run.

!!! warning "You cannot currently read an expiry back"

    A key's expiry is recorded on the key object, and no API returns it.
    `GET /auth/namespaces/{namespace}/keys` answers with a list of key
    *names* only, and the same is true of the scopes and the provenance of
    a key minted by the federated exchange.

    This makes the forensic window above more important than it should be:
    when automation stops authenticating, the evidence that the key lapsed
    is the key's disappearance and its events, not a field you can query.
    Setting `NAMESPACE_KEY_REAP_GRACE` to `0` while investigating keeps
    that evidence indefinitely.

    Tracked as
    [issue #3672](https://github.com/shakenfist/shakenfist/issues/3672).

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

This is a REST API feature which the command line does not expose yet:
`sf-client namespace add-key` takes the secret as a mandatory argument,
so there is no way to omit it. Until that changes, generation means
`curl`:

```bash
curl -X POST https://sf.example.com/auth/namespaces/myproject/keys \
  -H "Authorization: Bearer ${SF_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"key_name": "deploy"}'
{"key_name": "deploy", "key": "sfk_..."}
```

The same applies to `expiry`, which is also body-only.

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

Three fields are mandatory, and three more are optional:

| Field | Required | Notes |
|-------|----------|-------|
| `name` | Yes | How rules refer to this issuer. Treat it as durable |
| `issuer_url` | Yes | Compared to a token's `iss` claim **exactly**: no normalisation, no trailing-slash tolerance. Must be `https` |
| `audience` | Yes | Tokens must be minted for this. Usually your cluster's API URL |
| `jwks_uri` | No | Where the signing keys are published. Always taken from here, never from the token. Must be `https`. Left out, it is [discovered](#discovery) from the provider |
| `client_id` | No | The OAuth client identifier you registered at the provider for human login. A workload exchange never reads it |
| `oidc_scopes` | No | The OpenID Connect scopes to request at human login, as one space separated string such as `openid profile groups`. A workload exchange never reads it |

An issuer which only verifies workload tokens -- GitHub Actions, say --
has no client registration at its provider and never will, which is why
`client_id` and `oidc_scopes` are optional. An issuer with a `client_id` and a
discovered login endpoint is [login capable](#human-login-metadata).

Every one of `issuer_url`, `jwks_uri`, `audience`, `client_id` and
`oidc_scopes` may be up to 255 characters, which is what their columns
hold. Discovered endpoints may be up to 1024. A longer value is
refused with a **422** rather than stored, because a value between the
limit and the column was previously accepted and then truncated by the
database -- and a truncated `issuer_url` or `jwks_uri` means tokens
checked against the wrong issuer, or signing keys fetched from the
wrong place.

The exact `issuer_url` comparison is deliberate. A loose comparison
here is a way to accept tokens from somewhere else entirely, so if
your tokens are refused with an untrusted-issuer message, check for a
trailing slash before checking anything else.

Both URLs must begin with `https://`, in lower case, and anything
else is refused with a 400 on create and on update. (`HTTPS://` is
refused too: an `issuer_url` spelled that way could never equal a real
token's `iss`.) For `jwks_uri` the reason is direct: a JWKS
fetched over HTTP can be substituted by anyone on the path, which
turns signature verification into theatre. For `issuer_url` it is one
step further away -- the value is compared against a token's `iss`
claim rather than fetched -- but it names the identity whose keys the
cluster is about to trust, and OIDC discovery builds its request from
that same field, so the scheme is checked once here rather than again
in everything that reads it.

???+ warning "Rules reference issuers by name"

    Deleting an issuer does not delete the mapping rules that name it;
    those rules simply stop working. Recreating an issuer under the
    same name silently rebinds every rule that named it, potentially
    to a different identity provider than the rules' authors intended.
    Renaming rather than recreating avoids this.

### Discovery

Most identity providers publish an OpenID Connect discovery document
at `<issuer_url>/.well-known/openid-configuration`, saying where their
keys and login endpoints are. Shaken Fist fetches it so that you can
configure a provider by its issuer URL and audience alone.

Discovery runs when an administrator asks and at no other time: when
an issuer is created, when it is updated, and when it is explicitly
refreshed (below). It never runs on a timer and never while a request
is being served, because putting an identity provider on a path that
cluster operation depends on would make the cluster less reliable in
the name of making it more convenient. The price is that an endpoint
which moves at the provider stays stale here until somebody refreshes
the issuer.

What it fills in on the issuer:

* `jwks_uri`, when you did not supply one.
* `authorization_endpoint`, `token_endpoint` and
  `device_authorization_endpoint`, which a human login flow starts
  from. A provider which does not offer the device code grant simply
  publishes no device endpoint, and the issuer then has none. A
  successful run replaces all three, so a provider which stops
  publishing one stops being advertised as having it.
* `discovered_at`, the time of the last successful run.

It cannot fill in `client_id` or `oidc_scopes`. Those are a
registration you make at the provider, and no document says what you
registered.

The document's `issuer` member must equal the `issuer_url` you
configured **exactly**, character for character, or the document is
refused and nothing is taken from it. Without that, a document fetched
from one place could name endpoints belonging to somewhere else, and
users would be sent to log in there. The commonest cause of a refusal
is a trailing slash: Authentik's issuer ends in one
(`https://idp.example.com/application/o/sf/`) and Keycloak's does not
(`https://idp.example.com/realms/sf`), and the value you configure has
to be the one the provider writes in its own document, which is also
the one it writes in the `iss` claim of its tokens. The refusal names
both values. Shaken Fist takes care of the slash when it builds the
URL to fetch, so configuring Authentik's issuer with its slash does not
produce a doubled one.

The fetch is also refused if `issuer_url` is not `https://` or carries
a URL query or fragment (an OpenID Connect issuer identifier may have
neither, and the discovery URL is built by appending to it), if the
response is a redirect, is larger than
`FEDERATION_DISCOVERY_MAX_BYTES`, or takes longer than
`FEDERATION_DISCOVERY_FETCH_TIMEOUT_SECONDS` to send its body, or if
the document is not JSON, does not carry an `https://` `jwks_uri`, or
names an endpoint longer than this cluster can store. Redirects are not followed,
for the same reason and with the same symptom as the JWKS fetch: see
[The JWKS URI must be the final URL](#the-jwks-uri-must-be-the-final-url).
Certificate authorities are handled as they are for JWKS fetches too,
so `FEDERATION_JWKS_CA_BUNDLE` covers a private provider for discovery
as well.

???+ warning "Configure issuers after a deploy finishes, not during one"

    The discovery columns were added to `trusted_issuer_attributes` in
    the same release as this feature, so during a rolling upgrade a
    new `sf-api` may be talking to an `sf-database` that does not know
    them yet. A create in that window writes the three original
    columns and silently drops the `client_id` and the discovered
    endpoints, so the issuer comes back with them null and never
    appears in `GET /auth/login`. An update fails loudly instead,
    which is the better half of the behaviour.

    Nothing needs backfilling -- null correctly means "not discovered
    yet" -- but an issuer configured mid-deploy needs its `client_id`
    re-sent and its discovery re-run once the deploy has finished.

#### When discovery must succeed

If you did not supply a `jwks_uri`, the issuer depends on discovery to
have one, so a failed fetch fails the request with a **502** carrying
the reason, and nothing is created or changed. If this cluster cannot
build a TLS trust store at all (a bad `FEDERATION_JWKS_CA_BUNDLE`) the
answer is a **503** instead, because the provider was never asked.

If you did supply a `jwks_uri`, discovery is best effort: it is still
attempted, for the login endpoints, but a failure is logged, recorded
as an audit event on the issuer, and the request succeeds. This is what
keeps an issuer for a provider which publishes no document, or none
reachable from here, working exactly as it did before discovery
existed.

Your `jwks_uri` always wins. If the document names a different one,
yours is kept and the disagreement is recorded as an audit event
naming both. It is not refused because the right value is sometimes
deliberately not the published one, such as a provider whose published
JWKS path redirects to a CDN.

To skip the fetch entirely, send `"discover": false`. A `jwks_uri` is
then mandatory, since nothing else could supply it.

#### Re-running discovery

```bash
curl -X POST https://sf.example.com/auth/issuers/corporate/discover \
  -H "Authorization: Bearer ${SF_ADMIN_TOKEN}"
```

This fetches the document again and returns the issuer. It changes
the three endpoints and `discovered_at`, and fills in a `jwks_uri` the
issuer does not have yet. It never touches your `audience`,
`client_id` or `oidc_scopes`. Unlike
a create or an update it always treats a failure as a failure, with a
**502** or **503** as above, because a fetch is the only thing you
asked for and a 200 for one that did not happen would be a lie.

A refresh keeps the `jwks_uri` the issuer already has, whether you
supplied it or an earlier run found it. If a provider has moved its
keys, send an update without a `jwks_uri` so that discovery supplies
it afresh.

### Updating an issuer

An update (`PUT /auth/issuers/<name>`) replaces `issuer_url`,
`jwks_uri` and `audience` together, as it always has: they are one
coherent configuration, and a new `issuer_url` kept alongside a stale
`jwks_uri` is a broken issuer rather than a partly updated one.
Sending the update without a `jwks_uri` therefore does not keep the old
one; it asks discovery for a fresh one.

`client_id` and `oidc_scopes` behave differently. If the request does
not mention them they are **left alone**, and sending one as an empty
string **clears** it. They are a client registration at the provider
rather than part of the identity of the issuer, and every script
written before they existed sends an update without them, so
correcting an `audience` must not quietly turn off human login. An
explicit `null` counts as not mentioning it.

The discovered endpoints are left alone too when no discovery ran, or
when a best effort run failed. They are replaced only by a successful
run.

An issuer which ends a write with a `client_id` and no `oidc_scopes` is
stored with `oidc_scopes` set to `openid`, because every OpenID Connect
flow must request that scope and a registration without it is one no
client could use. This holds whichever request supplied the pieces: set
the scopes first and the client later, or the reverse, and you get the
same result. To manage the list yourself, send `oidc_scopes` as an
empty string in the same request; the default is not applied over an
explicit clear.

The default has no converse: clearing a `client_id` on its own leaves
the `oidc_scopes` behind. The issuer stops being login capable, because
`client_id` is the half that decides that, so nothing is served for it
and no flow breaks -- but the row keeps a scope list for a registration
that no longer exists. Clear both in the same request if you want the
row to read cleanly.

### Human login metadata

`GET /auth/login` lists the identity providers this cluster offers for
human login. It needs **no credential**, because a person about to log
in does not have one yet, and the endpoint which lists issuers for
administrators is not available to them. An issuer is **login
capable**, and appears, when it has a `client_id` and at least one of
an `authorization_endpoint` or a `device_authorization_endpoint`. An
issuer which only verifies workload tokens never appears. An empty
list means the cluster offers no human login.

Each entry serves `name`, `issuer_url`, `client_id`, `oidc_scopes` and
whichever of the three endpoints are set, and nothing else. In
particular the `jwks_uri`, the `audience`, and the issuer's uuid and state
are withheld. You should still know that it exists, because it tells
anyone who asks which identity provider your cluster trusts for human
login, and so where they might aim a phishing page. The provider's
endpoints are public at the provider anyway, which is why this is
judged acceptable.

Requests are rate limited per source address, using the same
`FEDERATION_RATE_LIMIT_PER_MINUTE` value as the exchange but a counter
of its own. The separation means a CI fleet behind one NAT address
exhausting its exchange budget does not stop people in the same office
logging in, nor the reverse. The same caveat about reverse proxies
applies as for the exchange. A limited caller gets a **429**, and if the
database cannot be reached to count the request, a **503**.

Setting `FEDERATION_RATE_LIMIT_PER_MINUTE` to `0` disables the limit on
this endpoint too, not only on the exchange. That is worth knowing
before you choose it: this endpoint reads one row per configured issuer
on every request and caches nothing, so with the limit off an anonymous
caller turns each request into a read per issuer with nothing counting
them. The exchange, by contrast, bounds its body and does its cheap
local checks before touching anything.

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

### A worked GitHub Actions example

GitHub can mint an OIDC token for a workflow job, describing the
repository, the branch and the workflow that asked for it. This is the
end to end shape of granting a repository's workflows scoped access to
a namespace.

The `sf-client` command line does not wrap these routes yet, so the
examples below call the REST API directly.

Configure the issuer once, as an administrator:

```bash
curl -X POST https://sf.example.com/auth/issuers \
  -H "Authorization: Bearer ${SF_ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
        "name": "github",
        "issuer_url": "https://token.actions.githubusercontent.com",
        "jwks_uri": "https://token.actions.githubusercontent.com/.well-known/jwks",
        "audience": "https://sf.example.com"
      }'
```

Then, as the owner of the namespace the workflow should reach, write a
rule saying which jobs qualify and what they get:

```bash
curl -X POST https://sf.example.com/auth/namespaces/ci/rules \
  -H "Authorization: Bearer ${SF_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
        "name": "ryll",
        "issuer": "github",
        "bound_claims": {
          "repository": "shakenfist/ryll",
          "ref": ["refs/heads/develop", "refs/heads/main"]
        },
        "scopes": ["blob.read", "artifact.*"],
        "key_ttl": 3600,
        "key_name_prefix": "ryll-ci"
      }'
```

The workflow needs `id-token: write` permission to ask GitHub for a
token, and nothing else:

```yaml
permissions:
  contents: read
  id-token: write

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Get a Shaken Fist key
        run: |
          IDENTITY=$(curl -sS \
            -H "Authorization: bearer ${ACTIONS_ID_TOKEN_REQUEST_TOKEN}" \
            "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=https://sf.example.com" \
            | jq -r .value)

          RESPONSE=$(curl -sS -X POST https://sf.example.com/auth/federated \
            -H "Content-Type: application/json" \
            -d "{\"token\": \"${IDENTITY}\",
                 \"namespace\": \"ci\",
                 \"rule\": \"ryll\"}")

          echo "::add-mask::$(echo "${RESPONSE}" | jq -r .key)"
          echo "SHAKENFIST_KEY=$(echo "${RESPONSE}" | jq -r .key)" >> "${GITHUB_ENV}"
          echo "SHAKENFIST_NAMESPACE=ci" >> "${GITHUB_ENV}"
```

The `audience` in the token request must match the issuer's
configured `audience` exactly, and the `add-mask` line matters: the
minted secret is a credential, and the response body is the only place
it will ever appear.

There is no repository secret anywhere in this. The credential the job
ends up holding is scoped to `blob.read` and `artifact.*`, expires an
hour after it was minted, and its provenance records which rule minted
it and which claims were satisfied -- so an audit of "what did that
workflow have access to" is a query rather than an investigation.

Nothing here is specific to GitHub's hosted runners; a self hosted
runner asks for its token the same way.

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

Every attempt is counted, including ones refused for naming an issuer
this cluster does not trust. Only the checks that read nothing but the
request itself -- a missing field, an oversized body -- happen before
the counter. So a misconfigured workflow pointed at the wrong issuer
consumes its source's budget in the same way a wrong key does, which
is worth knowing when a CI fleet sharing one NAT address starts
seeing 429s.

Both checks fail closed: if the database cannot be reached the
exchange answers 503 rather than assuming the request is fine.

### Settings

| Setting | Default | Notes |
|---------|---------|-------|
| `FEDERATION_JWKS_CACHE_SECONDS` | 300 | How long an issuer's published keys are cached. Lower shortens the window in which a revoked key is still accepted; higher reduces load on the provider. An unknown key id can also force a refetch before this expires, so raising this does not by itself delay recognising a rotated key |
| `FEDERATION_JWKS_ROTATION_COOLDOWN_SECONDS` | 30 | The shortest interval between a successful JWKS fetch and a refetch forced by an unrecognised key id, measured from any successful fetch and not only from a previous forced one. A rotation landing inside the window is not recognised until it elapses; without a window, every invented key id a stranger sends is another fetch. `0` fetches on every unrecognised key id |
| `FEDERATION_JWKS_FETCH_TIMEOUT_SECONDS` | 5 | How long to wait for an issuer's JWKS endpoint. The fetch happens while holding that issuer's refetch lock, so this is also the longest one unreachable provider can pin an API worker |
| `FEDERATION_DISCOVERY_FETCH_TIMEOUT_SECONDS` | 5 | How long to wait when fetching an issuer's discovery document. Separate from the JWKS timeout because discovery only runs when an administrator configures an issuer, where a slow provider costs that administrator one request, while the JWKS fetch can pin an API worker per anonymous exchange. Applied three times: to the connect, to each read, and as a deadline over the body once the response starts, so one fetch is bounded at roughly twice this value. Must be at least 1 |
| `FEDERATION_DISCOVERY_MAX_BYTES` | 65536 | Largest discovery document accepted, in bytes. The body is read up to this limit and refused beyond it, rather than trusting the provider's `Content-Length`, so a login page or a misbehaving proxy cannot hand us an unbounded response. A real document is a few kilobytes |
| `FEDERATION_MAX_TOKEN_BYTES` | 16384 | Largest exchange request accepted, refused before parsing. A real identity token is one to two kilobytes. A request with no `Content-Length` is refused with 411 rather than measured, so chunked encoding cannot opt out of the limit |
| `FEDERATION_RATE_LIMIT_PER_MINUTE` | 60 | Attempts allowed per source address per minute, counted separately for the exchange and for `GET /auth/login`. `0` disables rate limiting entirely, on both |
| `FEDERATION_JWKS_CA_BUNDLE` | *(empty)* | Path to a PEM bundle of extra certificate authorities to trust when fetching an issuer's JWKS. Empty means the system trust store alone |

### When an issuer rotates its signing keys

An identity provider replaces its signing keys periodically, and the
first warning a cluster gets is a token naming a key id that is not in
the cached key set. That is worth a refetch, and one happens -- but no
more often than `FEDERATION_JWKS_ROTATION_COOLDOWN_SECONDS`, because
`/auth/federated` takes no credential and an unrecognised key id is
something anyone can invent. Without a floor each invented key id
would be another fetch of the provider's JWKS, made by us, on someone
else's say-so.

The cost is bounded and worth naming: a rotation which happens just
after a successful fetch is not recognised until the window elapses,
and exchanges presenting the new key are refused in the meantime with
`no signing key for this token`. Thirty seconds is the default. Lower
it if an issuer rotates often enough for that to matter, or set it to
`0` to fetch on every unrecognised key id, accepting that a stranger
can then set the fetch rate.

When sizing the window, note that it starts at *any* successful fetch,
not only at a fetch an unrecognised key id forced. The first fetch of
an issuer's JWKS starts it, and so does the refetch after
`FEDERATION_JWKS_CACHE_SECONDS` expires -- so a rotation arriving ten
seconds after a routine cache-expiry refetch waits out the remaining
twenty, even though no forced fetch has ever happened for that issuer.

That also means setting it at or above `FEDERATION_JWKS_CACHE_SECONDS`
turns forced refetches off entirely rather than merely slowing them:
every cache-expiry fetch restarts the window before it can elapse, so
an unrecognised key id never gets to force anything and a rotation is
picked up only when the cache next expires. Nothing refuses that
configuration — it is a longer delay, not an incorrect decision — but
it is not what the two settings look like they do. Keep the cooldown
comfortably below the cache lifetime.

### The JWKS URI must be the final URL

Redirects are not followed when fetching an issuer's JWKS, so
`jwks_uri` has to be the URL which serves the key set rather than one
which points at it. A provider which answers `/.well-known/jwks` with a
301 or 302 to a CDN path needs the CDN path configured on the issuer.
This is a change in PyJWT 2.14, which builds its fetch opener with
redirects refused, so an issuer which worked before an upgrade to that
release can start failing at it.

The symptom is every exchange against that issuer answering 401 with
`no signing key for this token from issuer <name>: Fail to fetch data
from the url`, which is the same message an unreachable provider
produces. `curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n'
<jwks_uri>` distinguishes the two.

### An identity provider behind a private CA

A self hosted Authentik or Keycloak usually presents a certificate
signed by the organisation's own CA rather than a public one, and the
`jwks_uri` fetch will refuse it. Point `FEDERATION_JWKS_CA_BUNDLE` at a
PEM file of the extra authorities to trust, and place that file on
every node — any node may serve `/auth/federated`. The ansible
collection's `node` role takes this as `federation_jwks_ca_bundle`;
getting the file itself onto the nodes is yours to arrange, since it is
usually the same bundle the rest of your fleet already has. The same
bundle is used when fetching the provider's [discovery document](#discovery).

These anchors are **added** to the system ones rather than replacing
them, so configuring a private provider does not stop a public one such
as GitHub from verifying, and both can be trusted issuers at once.

Nothing else is relaxed by setting it. `jwks_uri` must still be
`https://`, the hostname must still match, expiry is still checked, and
a certificate chaining to neither set is still refused. There is
deliberately no option to skip verification: a JWKS fetched over a
connection nobody authenticated can be substituted by anyone on the
path, which makes signature verification theatre.

If the path is wrong, or the file is not a PEM bundle, `sf-api` says so
twice. Each worker complains at startup, so a bad path is a line in the
log at deploy time rather than a surprise at somebody's first federated
login weeks later. If it is still wrong when a token arrives,
`/auth/federated` answers **503** rather than 401 — the token was never
examined, and calling it rejected would send the caller to their
identity provider to look for a fault which is in your config file. The
log line names `FEDERATION_JWKS_CA_BUNDLE` and the path; the response
deliberately does not.

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
* when our CI system creates a new CI runner and associated namespace, it
  creates a trust between that ephemeral namespace and the `ci-images` namespace.
* jobs to create new images build them in their local namespace, and then "gift"
  them to the `ci-images` namespace via a label.
* jobs which need to boot a test image can now see the images from the `ci-images`
  namespace by virtue of this trust relationship.

### What a trust does and does not grant

A trust grants **visibility** over your *resources* — instances, networks,
artifacts and the like. It is the system namespace's ability to see across
namespaces, scaled down. A namespace you trust can list and read those
objects. It cannot delete them, rename them, share them, or change their
metadata — those all require the object's own namespace, or `system`.

A trust also does not outlive the namespace it names. Deleting a namespace
revokes every trust pointing at it, on every other namespace, at the moment
of deletion. This matters because a trust names a namespace by name and
namespace names can be reused once the cluster has finished cleaning the
deleted one up: without the revocation, whoever created that name next would
inherit your trust without you doing anything or being told. You will see a
`trust revoked, trusted namespace is gone` event on your namespace when this
happens.

For the same reason you cannot grant a trust to a namespace which does not
exist: the request is refused with `external namespace not found`. Create the
namespace first. The cluster maintainer also checks hourly for trust entries
naming namespaces which are gone and removes them, with the same event, which
is how entries predating these checks are cleaned up.

Giving is a separate question from taking, and it is still allowed: a
namespace you trust may *create* an object in your namespace, which is exactly
the "gift" step in the `ci-images` example above. Creation is additive, you
opted into it by extending the trust, and nothing you already had is lost by
it. Deletion is none of those things.

Giving the same thing twice is not creation, though, and it is worth being
precise about where the line falls. A trusted namespace may upload an artifact
into yours when nothing of that `source_url` is there yet. It may not upload
again over the artifact that first upload produced: adding a version ends in
`delete_old_versions`, the caller supplies the blob, and the practical result
is that an instance of yours booting that artifact afterwards gets somebody
else's image. So the second upload is refused, and a CI job which pushes a
nightly image into a shared namespace needs a key in that namespace rather
than a trust.

Labels fall on the same side of that line, which they did not until v0.8.
A label may be named `<namespace>/<label>`, so a caller *can* reach into
your namespace through one, and nothing used to stop it — updating a label
in your namespace did not even require a trust. A trusted namespace may now
create a label in yours and may not update one that already exists, matching
artifacts exactly. This does affect the `ci-images` pattern above: the first
gift of a given label works, and a job which republishes under the same label
name every night needs a key in the receiving namespace.

**Namespace administration is the exception, and it is a large one.**
Adding a key to your namespace, and writing a mapping rule in it, are both
gated on this same trust relationship rather than on ownership. A namespace
you trust can therefore mint credentials in yours — directly with
`add-key`, or by writing a mapping rule, which is a standing offer to mint
a key for anyone holding a matching identity token. It can also delete the
rules you wrote.

So a trust is not only a window onto your resources; it is administrative
access to your namespace's credentials. Extend one to a namespace you would
be willing to hand a key to, and read
[Federated identity](#federated-identity) before extending one to a
namespace that has mapping rules configured.

!!! note

    Artifacts behaved differently until recently: a trusted namespace could
    delete another namespace's artifacts. Instances and networks never
    permitted this, and artifacts now match them. If you have tooling which
    relied on deleting artifacts across a trust, it needs a key in the owning
    namespace, or the `system` namespace, instead.
