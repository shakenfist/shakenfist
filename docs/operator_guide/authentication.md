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

### Reading a key's expiry and scopes

A key's expiry and scopes are readable by any caller who can read the
namespace: one authenticated to the namespace itself, to `system`, or to a
namespace the target trusts.
`GET /auth/namespaces/{namespace}/keys` still answers with a bare list of key
names, and omits keys which have expired. Adding `?detail=true` answers with
one object per key instead, and `GET /auth/namespaces/{namespace}/keys/{key_name}`
answers with the same object for a single key. Both detailed reads include
expired keys, so "which of my keys lapsed, and when" can be answered without
reading the database. `sf-client namespace show` renders them too, with
`provenance` in its table and JSON output rather than in its comma separated
one, where a serialised object would be ambiguous. That comma separated output
gives the expiry as the raw unix timestamp the cluster sent, so a script can
compare it with the current time without parsing a date.

The detailed reads never return the key's secret. A key with no scopes
recorded reports `scopes` as null, and that means the key is **unrestricted**
rather than unprivileged: such a key mints wildcard tokens. There is no state
field, so a key the expiry sweep has already soft deleted still appears, with
an expiry in the past, until it is hard deleted -- `NAMESPACE_KEY_REAP_GRACE`
plus `CLEANER_DELAY` after it lapsed, which is the forensic window described
above.

`provenance`, the record of who or what minted a key, is served only to a
caller authenticated to the `system` namespace. Every other caller who can read
the namespace gets the remaining fields but not `provenance`, because it can
carry the identity of a person. That audience is wider than the namespace's own
callers -- trusting another namespace lets its callers list these keys too -- so
serving provenance to all of them would publish who logged in, and when, to each
of them. Weigh that when you grant trust. A missing `provenance` means the
caller is not in `system`, not that the key has none. See
[a minted key records who it was minted for](#human-login-mints-credentials)
for what provenance contains.

Note that this gate is the namespace alone, not the namespace and a scope.
**Scoping a `system` credential does not withhold provenance from it**: a key
minted into `system` and scoped to only what these reads need still sees the
field, which is deliberate, because an operator administering keys with a
correctly narrowed credential is exactly who the field is for. The namespace
event trail carries the same subjects and claims and is served behind the
stricter test of `system` plus `cluster-admin`, so the two surfaces disagree
on purpose. If you need a `system` credential which cannot read who logged in,
there is no scope which arranges that today.

Even so, the forensic window above is worth keeping in mind: setting
`NAMESPACE_KEY_REAP_GRACE` to `0` while investigating keeps an expired key
readable indefinitely.

## Watching for a namespace with no usable key

A namespace with no key which can authenticate cannot be reached at all,
and nothing in Shaken Fist can refuse to let that happen: an operator may
delete the last key by hand, and the expiry sweep above soft-deletes the
last key if nothing replaced it. When the namespace is `system`, every
deploy, every `sf-ctl` call and every piece of automation holding a system
credential loses it at the same moment -- which once took 66 minutes to
diagnose, because the first symptom was an outage rather than an alert.

The elected cluster daemon therefore counts, every fifteen minutes, how
many keys could authenticate right now:

- **`namespace_usable_keys`** — a gauge on `CLUSTER_METRICS_PORT`
  (default `13007`), labelled by `namespace`. A key counts when its
  object state is active, its attributes row is present and its expiry
  has not lapsed, which are the three terms the authentication read path
  itself joins on. Every active namespace is published, zeros included,
  so an alert can rely on the series existing.

Only the node holding the cluster maintenance lock publishes it, so scrape
every node which runs `sf-cluster`. Like the other leader-only gauges it is
dropped on a lost election, and a pass whose count fails drops it too
rather than publishing a zero it cannot vouch for -- zero is the condition
you are alerting on, so a database blip must not be able to manufacture
one. That means the absence of the series is its own alert, and the
shipped rules in
[`examples/prometheus-namespace-key-alert.yaml`](https://github.com/shakenfist/shakenfist/blob/develop/examples/prometheus-namespace-key-alert.yaml)
cover both:

```
max by (namespace) (namespace_usable_keys{namespace="system"}) == 0
```

A namespace with no keys at all is unremarkable -- a federated one mints
them per login -- so only `system` is worth alerting on by default. The
cluster daemon log says so as well, once per pass, for `system` alone.

`sf-ctl namespace-key-health` asks the same question of the database
directly and exits non-zero when `system` has no usable key. That is the
surface to reach for once the condition has arrived, because by then the
API cannot be asked anything; recover with `sf-ctl bootstrap-system-key`,
which also heals a key whose state row has gone missing.

Rotating a single-key namespace by deleting before creating can be caught
mid-rotation. That is a true reading rather than a false alarm, but it is
avoidable: create the new key first, then delete the old one.

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

That old column has since been dropped, by the same command, one schema
version later. It had no reader left, and a cluster which keeps a second copy
of live bcrypt hashes and nonces at rest is carrying an exposure for nothing.
The two steps run in the right order within one run of the command, so a
cluster upgrading from before first-class keys copies its keys out of the
column before it is dropped, however many releases it skips.

The migration is one-way, and the drop makes that sharper. Before the drop,
downgrading to a release that predates first-class keys lost keys created
*after* the migration and kept the ones that predated it. After the drop
there is no column for such a release to read, so it sees **no keys at all**
and refuses every credential until one is created again on the older release.
If you keep a rollback plan which crosses this upgrade, plan to re-create
keys as part of it, or take a database backup first: the keys are not
recoverable from the new tables by a release which does not know about them.

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
| `oidc_scopes` | No | The OpenID Connect scopes to request at human login, as one space separated string such as `openid profile`. What a provider needs here differs; the worked examples below show two. A workload exchange never reads it |

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
  from. A provider which does not implement the device code grant
  publishes no device endpoint, and the issuer then has none. A
  provider which implements it but has not configured it is a
  different case, and may publish the endpoint anyway -- see below. A
  successful run replaces all three, so a provider which stops
  publishing one stops being advertised as having it.
* `discovered_at`, the time of the last successful run.

It cannot fill in `client_id` or `oidc_scopes`. Those are a
registration you make at the provider, and no document says what you
registered.

The three endpoints say what the provider published rather than what it
will honour: a provider can advertise a `device_authorization_endpoint`
for a flow it has not actually configured, and serve a document
indistinguishable from a working one's. So an issuer which appears
device capable here proves that the provider published an endpoint, not
that a login through it will complete, which is why the
[Keycloak](#a-worked-keycloak-example) and
[Authentik](#a-worked-authentik-example) examples below name the
provider side switches that decide.

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

### Human login mints credentials

`POST /auth/login` is the other half of human login. A person who has
completed a flow at your identity provider posts the identity token they
came back with, and receives one namespace key for every mapping rule their
claims satisfy. Like `GET /auth/login` it needs **no credential**, for the
same reason.

A person's entitlements are therefore exactly the [mapping rules](#delegating-to-namespace-owners)
they satisfy, and nothing else: Shaken Fist has no list of users to consult.
A credential is one namespace deep, so a person entitled to three namespaces
gets back three keys, and presents whichever one the namespace they are
addressing needs. Each key is an ordinary namespace key carrying the scopes
and lifetime its rule grants, and its provenance records that it came from a
login. Nothing about authorisation changes once a key exists.

**This is an administrative act, and the rules are where it happens.** A
mapping rule in the `system` namespace is exactly the standing offer of
administrative credentials described under
[delegating to namespace owners](#delegating-to-namespace-owners), and for
human login that means a group membership at your identity provider is
cluster administration. Nothing here treats `system` specially except to make
it visible: a key minted into it is audited as an *administrative key minted
by human login* on the key, and the namespace's event log says *human login
minted an administrative key*. Those events are worth alerting on. A cluster
with no rule in `system` never mints into it.

???+ warning "Human login verifies the `client_id`, not the `audience`"

    The `audience` and `client_id` columns on an issuer are stamped into
    tokens by different parties, and a login is verified against the second.

    `audience` is what a **workload** asks for: a GitHub Actions job requests
    a token "for" whatever string you tell it to, which is why the guide
    suggests your API URL. `client_id` is what the identity provider stamps
    into a **person's** token on its own, because an OpenID Connect identity
    token's `aud` is the application it was issued to, and that is the client
    registration you made at the provider. Nobody gets to choose it at login
    time.

    So if you set `audience` to your API URL, registered a client, and
    every login is refused, this is why: the token your provider issued
    names your client, not your API URL, and login checks the former. The
    reverse also holds. A person's token is not accepted by the workload
    exchange, and a workload's token is not accepted by login, unless you have
    deliberately set the two columns to the same string. The fix is to check
    that `client_id` is the identifier the provider actually issues
    tokens for, not to change `audience`.

An issuer is login capable only with a `client_id` **and** at least one of an
`authorization_endpoint` or a `device_authorization_endpoint`, exactly as for
[`GET /auth/login`](#human-login-metadata); a token from any other issuer is
refused, even though the workload exchange would accept it. Both endpoints
share the one predicate, so what the listing advertises is what the login
accepts.

**Refusals tell the caller nothing.** Every refusal other than the ones below
answers **401** with the same terse message -- an untrusted issuer, a token
that fails validation, a token that matches no rule, a replayed token, an
issuer not configured for login. This is deliberate: the caller is
anonymous, and distinguishing "no rule matches you" from "your token is bad"
would turn the endpoint into an oracle for whether a given identity matches
anything on your cluster. The reason is written to the `sf-api` log at info
level, prefixed `Login refused:`, which is where you look when somebody
reports that they cannot log in. A person who has simply not had a rule
written for them is refused in the same way.

The exceptions are failures that belong to the cluster rather than the
caller. A **503** means the database could not be reached, the mapping rule
states could not be read (the login is refused rather than guessed at,
because guessing wrong would mint from rules an administrator had deleted),
or the file named by `FEDERATION_JWKS_CA_BUNDLE` could not be loaded.

**Replay.** An identity token may be used to log in once. A retry with the
same token is refused with a 401 and mints nothing, including after a login
which failed part way through; the person asks their provider for another
token.

**The cap.** Each key costs a bcrypt hash, so one login may mint at most
`FEDERATION_MAX_LOGIN_CREDENTIALS` keys. A person whose claims satisfy more
rules than that is refused with **507**, not given a truncated set, because
they could not tell a short set from a complete one. They can do nothing about
it themselves; you must either narrow your rules or raise the setting. The log
line names the setting and both counts. Only rules which are active, for this
issuer, and whose claims match are counted.

**Keys accumulate.** Logging in again mints a fresh set rather than replacing
the last, because a login on a laptop must not invalidate the credentials a
session on a workstation is still using. An expired key stops working
immediately, but it is not removed immediately: the cluster daemon soft
deletes it `NAMESPACE_KEY_REAP_GRACE` seconds after it expired, and it is then
hard deleted once it has been soft deleted for `CLEANER_DELAY` seconds. With
the defaults that is roughly two hours after expiry, on top of the rule's key
lifetime, because both settings default to an hour. A person who logs in often
leaves up to `FEDERATION_MAX_LOGIN_CREDENTIALS` keys per login for the whole of
that window, which is worth knowing when you size a cluster for many users. See
[key expiry and reaping](#key-expiry-and-reaping).

**A minted key records who it was minted for.** The key's provenance
carries the person's subject identifier, the token's `jti`, and the claims
that actually matched. Provenance is served only to a caller in the `system`
namespace (see [reading a key's expiry and scopes](#reading-a-keys-expiry-and-scopes)),
so a namespace owner cannot read it back from their own keys. The grant stays
auditable to an administrator, and that is why the key's *name* carries a
random discriminator instead of the person's identity: the name is visible to
everyone who can list the namespace's keys. Even so, the claims you bind are
recorded
on every key the rule mints, so bind the narrowest claim that expresses the
entitlement, usually a group, rather than an address.

Requests are rate limited per source address like the others, on a counter
of their own, so a burst of logins does not throttle CI and the reverse. As for
the exchange and the listing, setting `FEDERATION_RATE_LIMIT_PER_MINUTE` to
`0` disables the limit, and a login is the most expensive of the three
unauthenticated routes: a JWKS fetch and then a bcrypt hash per credential.

"Source address" means the address `sf-api` itself sees, which is the
caveat in [abuse resistance](#abuse-resistance) and it bites harder here
than on the exchange. Behind a reverse proxy or NAT that does not preserve
the client address, every person on the site shares one budget rather than
holding one each, and the symptom is everybody getting 429s together at the
start of the working day while nothing is wrong with any of their tokens.
The three counters are separate budgets but take their size from the one
`FEDERATION_RATE_LIMIT_PER_MINUTE` value, so raising it for logins raises
it for the exchange too.
A limited caller gets a **429**.

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

### A worked Keycloak example

Keycloak is the less surprising of the two self hosted providers to
configure for human login, and this is the whole of it: a realm, a public
client, a group, one mapper, and two `curl` calls at the cluster. Produced
against **Keycloak 26.8.2**, which was configured through its admin REST
API; the console labels below are that release's, and each one is given with
the API attribute it sets so the attribute is the authority.
The cluster side is `curl` for the reason given in the
[GitHub Actions example](#a-worked-github-actions-example).

At the provider, as a Keycloak administrator:

1. **Create a realm**, `sf`, under *Manage realms*. The realm name is the
    last element of the issuer URL, so the issuer becomes
    `https://idp.example.com/realms/sf`.

2. **Create a group**, `sf-admins`, under *Groups*. Membership of this group
    is what will entitle a person to a namespace.

3. **Create a client**, `shakenfist`, under *Clients*, with *Client
    authentication* **off** — a public client. The thing registering is a
    command line program on a person's laptop, so a confidential client's
    secret would have to be handed to everyone who logs in, at which point it
    is not a secret. The device grant requires a public client anyway, and
    there is nothing here for a secret to protect: `POST /auth/login` verifies
    a token's signature and its `client_id`, not a client secret.

    Three settings on the client matter.

    * *Valid redirect URIs*: `http://127.0.0.1:*`. `sf-client` binds an
      ephemeral loopback port for the browser flow, so the redirect URI it
      sends cannot be registered exactly; Keycloak's trailing `*` matches it.
    * *OAuth 2.0 Device Authorization Grant*, under *Authentication flow*:
      **on** (`oauth2.device.authorization.grant.enabled`). That checkbox is
      the whole of enabling the device flow at Keycloak, which has nothing at
      a realm or brand level to configure.
    * *PKCE Method* (`pkce.code.challenge.method`): **leave it empty**, for
      the reason in the warning below.

4. **Add a group membership mapper to the client**, under *Clients* ->
    `shakenfist` -> *Client scopes* -> `shakenfist-dedicated` -> *Add mapper*
    -> *By configuration* -> *Group Membership*, and check that *Add to ID
    token* (`id.token.claim`) is on. Keycloak ships no group claim at all, so without this mapper
    there is nothing to bind a rule to; and with it pointed only at the access
    token there is a claim visible at `/userinfo` while every login is still
    refused, because the identity token is the only token Shaken Fist ever
    sees.

5. **Create the person**, under *Users*, with *Email verified* on, then set
    their password under *Credentials* with *Temporary* off. Each alternative
    puts a required action on the account, which stops the login on an extra
    screen before any token is issued.

6. **Join them to the group**, under the user's *Groups* tab.

The mapper's *Full group path* switch (`full.path`) decides what the claim
says and it defaults to on, so a mapper created through the console writes
`"groups": ["/sf-admins"]` — with a leading slash. The slash is there because
the value is a group *path*: a group nested under another is `/parent/child`,
which is the real reason to leave full paths on. Whichever form you choose,
the rule below has to bind the string the token actually carries.

`groups` is **not** a scope Keycloak knows, and asking for one is a hard
refusal rather than a quietly ignored request:

```json
{
  "error": "invalid_scope",
  "error_description": "Invalid scopes: openid profile groups"
}
```

So the issuer's `oidc_scopes` is `openid profile`. The claim arrives anyway,
because a mapper on the client's own dedicated scope is always in scope. A
realm client scope literally named `groups` can be created instead, and then
`openid profile groups` is accepted — but attached to the client as an
*optional* scope that is a trap, because a login which does not ask for it by
name gets no claim, no rule match, and the deliberately uninformative 401.

Keycloak publishes its `device_authorization_endpoint` **per realm**, whether
or not the client you registered has the grant enabled, so the issuer is
advertised as device capable either way. The device endpoint itself is clear
about it when asked, which is more than the cluster can be, and running that
one request is quicker than suspecting the cluster:

```json
{
  "error": "unauthorized_client",
  "error_description": "Client is not allowed to initiate OAuth 2.0 Device Authorization Grant. The flow is disabled for the client."
}
```

???+ warning "Requiring PKCE at the client breaks only the device flow"

    Setting the client's *PKCE Method* (`pkce.code.challenge.method`) to
    `S256` makes Keycloak **require** a challenge on every request,
    including the device authorization request, which has nowhere to put one:

    ```json
    {
      "error": "invalid_request",
      "error_description": "Missing parameter: code_challenge_method"
    }
    ```

    The authorization endpoint is unaffected, so the symptom is a device
    login which cannot start on a client whose browser login works.
    `sf-client` sends an S256 challenge on the browser flow of its own
    accord, so leaving this setting empty costs no protection and is what a
    working device flow needs. "Require PKCE" is exactly the box a careful
    operator ticks, which is why it is worth naming.

Configure the issuer once, as a cluster administrator:

```bash
curl -X POST https://sf.example.com/auth/issuers \
  -H "Authorization: Bearer ${SF_ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
        "name": "corporate",
        "issuer_url": "https://idp.example.com/realms/sf",
        "audience": "https://sf.example.com",
        "client_id": "shakenfist",
        "oidc_scopes": "openid profile"
      }'
```

No `jwks_uri` is sent, so [discovery](#discovery) supplies it along with the
three login endpoints. `audience` is mandatory, and a login does not check
it: a real Keycloak identity token's `aud` is the bare client id, which is
why `client_id` is set here as well and why [human login verifies the
`client_id`](#human-login-mints-credentials) rather than the audience.

Then, as the owner of the namespace those people should reach:

```bash
curl -X POST https://sf.example.com/auth/namespaces/ops/rules \
  -H "Authorization: Bearer ${SF_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
        "name": "corporate-admins",
        "issuer": "corporate",
        "bound_claims": {"groups": "/sf-admins"},
        "scopes": ["instance.*"],
        "key_ttl": 3600,
        "key_name_prefix": "corporate"
      }'
```

`bound_claims` names `/sf-admins` with its leading slash, because that is
what this provider's token says. The claim is an array even for a person in
exactly one group, and a rule matches when any element of it equals the
string bound, so one group out of a person's twenty is enough; only the
elements which matched are recorded in the key's provenance.

The person then runs:

```bash
sf-client login
```

which prints a short code and a URL for them to open. Tell them that
Keycloak asks them to confirm **after** they have signed in: a *Yes* / *No*
consent page which belongs to the device grant itself rather than to the
client's *Consent required* setting, and which appeared here with that setting
off. A login described as "sign in and you are done" leaves them looking at
it while the client polls. Once they accept, they come back with one key per
namespace their groups entitle them to.

### A worked Authentik example

Authentik needs more switches than Keycloak, and two of them are invisible
from outside: a stock install advertises a device flow it cannot complete, and
serves a discovery document which says nothing is wrong. Produced against
**Authentik 2026.8.6**, configured through its API; the interface labels below
are that release's, and each field is given with the API name it sets.

At the provider, as an Authentik administrator:

1. **Replace the brand's web certificate**, before anything else. Authentik
    serves https from first boot with a certificate it generates itself, and
    that certificate carries no `subjectAltName` — so a cluster fetching its
    discovery document refuses it by hostname even when that same certificate
    is the trust anchor:

    ```
    certificate verify failed: Hostname mismatch, certificate is not valid
    for 'idp.example.com'
    ```

    Pointing `FEDERATION_JWKS_CA_BUNDLE` at the certificate does not help,
    because trusting a certificate does not give it a name; only replacing it
    does. Upload a keypair whose certificate names the provider's hostname in
    a `subjectAltName`, under *System* -> *Certificates*, and set it as the
    brand's *Web Certificate* (`web_certificate`) under *System* -> *Brands*.
    The running server
    picks it up within seconds and needs no restart.

2. **Create a group**, `sf-admins`, under *Directory* -> *Groups*. The
    group's **name** is what reaches the token: there is no path and no uuid
    in the claim.

3. **Create the person**, under *Directory* -> *Users*, with the group
    assigned, and *Set password* from the user's own menu.

4. **Create an OAuth2/OpenID provider**, under *Applications* ->
    *Providers*. The fields which matter are mostly the ones a newly created
    provider does not have.

    * *Client Type* (`client_type`): **public**, for the same reasons as the
      other provider.
    * *Client ID* (`client_id`): `shakenfist`. This is the string the issuer's
      `client_id` must be.
    * *Authorization Flow* and *Invalidation Flow*: both required, with no
      defaults. A stock install ships
      `default-provider-authorization-implicit-consent` and
      `default-provider-invalidation-flow`.
    * *Signing Key* (`signing_key`): empty by default, and without one there
      is nothing to sign an RS256 identity token with. The self signed certificate
      Authentik generated on first boot is a perfectly good signing key even
      though it was a poor web certificate.
    * *Scopes* (`property_mappings`): the shipped `openid`, `profile` and
      `email` scope mappings. `profile` is the one which carries the group
      claim, and the default is no mappings at all.
    * *Grant Types* (`grant_types`): must list the device code grant alongside
      `authorization_code` and `refresh_token`. The default is the empty
      list, and see the warning below for what that costs.
    * *Redirect URIs/Origins (RegEx)* (`redirect_uris`): one entry whose mode
      is *Regex* rather than *Strict*, with the value
      `http://127\.0\.0\.1:[0-9]+/`. The mode is the part to get right:
      `sf-client` binds an ephemeral loopback port, and only a regex entry can
      match one. The other provider's spelling,
      `http://127.0.0.1:*/`, is accepted here as a `strict` entry and then
      matches nothing, which answers the authorization request with a 400
      and an HTML error page rather than a redirect — correctly, since the
      redirect URI is the thing in doubt — and logs
      `redirect_uri_no_match`. It is a real regular expression, so the dots
      have to be escaped or they match any character.
    * *Include claims in id_token* (`include_claims_in_id_token`): leave it
      on, which is the default. Off,
      the scope mappings' claims are served from `/userinfo` and in the
      access token but not in the identity token, which is the only token
      Shaken Fist sees.

5. **Create an application**, under *Applications* -> *Applications*, with
    the slug `sf`, and bind the provider to it. The slug is the last element
    of the issuer URL, so the issuer becomes
    `https://idp.example.com/application/o/sf/` — including the trailing
    slash, which is part of the issuer and which [discovery](#discovery)
    compares character for character. The slugs `authorize`, `token`,
    `device`, `userinfo`, `introspect` and `revoke` are reserved, because the
    global endpoints live at those paths.

6. **Create a device code flow and point the brand at it.** Under *Flows and
    Stages* -> *Flows*, create a flow whose *Designation* is *Stage
    Configuration* and whose authentication is *Require authentication*;
    nothing needs to be bound to it, and an empty flow is enough. Then under
    *System* -> *Brands*, edit the brand in use and set that flow as its
    *Device code flow* (`flow_device_code`). This is not the default and it is
    not optional.

Authentik needs no mapper for the group claim: the shipped `profile` property
mapping already emits `"groups": ["sf-admins"]` — group names, as an array,
with no path and no prefix, which is not the same string the other provider
writes for the same group. What it needs instead is for `profile` to actually
be **granted**, so the issuer's `oidc_scopes` must be at least
`openid profile`. A login asking for `openid` alone gets no groups, no rule
match, and the uninformative 401. `groups` is not a scope here and asking for
one would be wrong; the claim rides on `profile`. Beware also that a request
naming no scopes at all is treated as asking for every scope the provider
has, so a hand rolled test of this configuration can pass while `sf-client`,
which always names its scopes, fails.

???+ warning "Discovery cannot tell you if an Authentik device login works"

    Authentik's discovery document carries a
    `device_authorization_endpoint`, and lists the device code grant in its
    `grant_types_supported`, whether or not a device login can complete. The
    document served by a provider with no device grant and a brand with no
    device code flow is byte for byte identical to the one a working
    configuration serves, so the issuer is advertised as device capable
    either way and nothing the cluster can read tells the two apart.

    With the **brand's device code flow** missing, the failure lands on the
    person rather than on the client, and only after they have typed their
    password. The device code request succeeds and looks perfectly normal;
    the verification URL sends them through an ordinary login; and the page
    they are returned to answers a bare **404 with an empty body**. No
    message, no error page. The reason is in Authentik's log and nowhere
    else:

    ```
    "event": "Brand has no device code flow configured",
    "logger": "authentik.providers.oauth2.views.device_init",
    "status": 404
    ```

    Meanwhile the client polls with `authorization_pending` until the code
    expires, which by default is sixty seconds later.

    With the **provider's grant types** missing the device grant, the device
    code request itself is refused, at the client rather than at the person,
    with a message that names something else entirely:

    ```json
    {
      "error": "invalid_client",
      "error_description": "Client authentication failed (e.g., unknown client, no client authentication included, or unsupported authentication method)"
    }
    ```

    Neither state is visible from here, so when a device login fails against
    an Authentik issuer, check those two settings at the provider before
    suspecting anything about the cluster.

Configure the issuer once, as a cluster administrator:

```bash
curl -X POST https://sf.example.com/auth/issuers \
  -H "Authorization: Bearer ${SF_ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
        "name": "directory",
        "issuer_url": "https://idp.example.com/application/o/sf/",
        "audience": "https://sf.example.com",
        "client_id": "shakenfist",
        "oidc_scopes": "openid profile"
      }'
```

As with the other provider, `jwks_uri` is left to [discovery](#discovery),
and the `aud` of a real Authentik identity token is the bare client id
rather than the audience — so [`client_id` is the field a login is verified
against](#human-login-mints-credentials). Authentik decides its issuer URL
from the `Host` header of the request rather than from a setting, so an
issuer configured from a document fetched through the wrong name is silently
wrong; fetch it the way your users will reach it.

Then, as the owner of the namespace those people should reach:

```bash
curl -X POST https://sf.example.com/auth/namespaces/ops/rules \
  -H "Authorization: Bearer ${SF_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
        "name": "directory-admins",
        "issuer": "directory",
        "bound_claims": {"groups": "sf-admins"},
        "scopes": ["instance.*"],
        "key_ttl": 3600,
        "key_name_prefix": "directory"
      }'
```

`sf-admins` here, with no leading slash, is the whole of the difference from
the other provider's rule: a rule binds the string its provider emits, and a
value copied between the two matches nothing.

The person then runs:

```bash
sf-client login
```

Using the URL with the code already in it, they never see a code entry page
— but the brand's device code flow is still what makes that route exist,
which is why it cannot be skipped. They do see one button to press at the
end, which is the device grant's own confirmation step and appears even though the
provider's authorization flow is the implicit consent one. The same shape as
the other provider, reached from the other direction.

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
| `FEDERATION_MAX_TOKEN_BYTES` | 16384 | Largest exchange or login request accepted, refused before parsing. A real identity token is one to two kilobytes. A request with no `Content-Length` is refused with 411 rather than measured, so chunked encoding cannot opt out of the limit |
| `FEDERATION_RATE_LIMIT_PER_MINUTE` | 60 | Attempts allowed per source address per minute, counted separately for the exchange, for `GET /auth/login` and for `POST /auth/login` -- three budgets, but one value sizing all three, so raising it for one raises it for the others. `0` disables rate limiting entirely, on all three, including the per-issuer read that the listing does on every request |
| `FEDERATION_MAX_LOGIN_CREDENTIALS` | 16 | The most namespace keys one `POST /auth/login` may mint, minimum 1. Each key costs a bcrypt hash, so without a cap one valid identity token commands as much deliberately slow work as the cluster has matching mapping rules. A login matching more rules than this is refused with 507 rather than truncated. Raising it raises the work one token can command; `FEDERATION_RATE_LIMIT_PER_MINUTE` bounds how often a source may try, and `0` removes that bound for every unauthenticated endpoint in this family. The two multiply, which is the number to size `sf-api` against: at both defaults one source address can command up to 960 bcrypt hashes a minute, each needing a fresh unreplayed token |
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
