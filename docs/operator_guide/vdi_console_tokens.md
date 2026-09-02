# VDI console tokens (Kerbside integration)

Since v0.8, Shaken Fist can hand a user a proxied graphical console instead
of requiring direct network access to the hypervisor. Rather than exposing
the raw SPICE port, Shaken Fist mints a short lived, cryptographically
signed token and points the user's viewer at a
[Kerbside](/components/kerbside/) proxy, which validates the token and
relays the SPICE session on the user's behalf.

This page is the operator runbook for that integration: what it is, how to
enable it, how the signing key is stored and rotated, and how the per-node
certificate subject flows through to the proxy. The user-facing side (how a
person actually opens a console) lives in the
[consoles user guide](/user_guide/consoles/).

## What the integration does

When a user requests a proxied console, `sf-api` mints an Ed25519 JSON Web
Token (JWT, algorithm `EdDSA`) describing the instance, the requesting
namespace, and an expiry. It returns a URL of the form
`<KERBSIDE_URL>/sf-console.vv?token=<jwt>`. The user's viewer fetches that
URL from Kerbside, which:

* verifies the token's signature **offline** against the public key Shaken
  Fist publishes — there is no callback to `sf-api` on the connection hot
  path;
* checks the audience, expiry, and single-use identifier (`jti`); and
* proxies the SPICE session to the correct hypervisor, pinning the backend
  certificate subject to the value Shaken Fist published for that node.

The trust model is therefore **offline signature verification**: Shaken
Fist is the sole signer, Kerbside is a verifier that never holds private
key material, and a compromised proxy cannot mint valid console tokens.

???+ note

    Two unrelated `KERBSIDE_*` configuration namespaces exist and must not
    be conflated. `KERBSIDE_URL` and `KERBSIDE_TOKEN_DURATION` described on
    this page are **Shaken Fist cluster configuration** (set via
    `SHAKENFIST_`-prefixed environment, `cluster_config`, or `sf-ctl`). The
    Kerbside proxy daemon has its own, separate `KERBSIDE_`-prefixed
    environment variables (documented in the
    [Kerbside configuration page](/components/kerbside/configuration/)),
    such as `KERBSIDE_SF_CONSOLE_TOKEN_AUDIENCE`. When you see a
    `KERBSIDE_*` name, check which side owns it.

## Enabling the integration

The integration is **disabled by default**: `KERBSIDE_URL` is empty, and
every mint request returns HTTP 404 while it stays empty. Instances still
offer the direct-to-hypervisor console described in the
[consoles user guide](/user_guide/consoles/); enabling this integration only
adds the proxied path.

!!! note

    Kerbside deployment itself is not yet automated by the Shaken Fist
    ansible collection, so enabling the integration is a **manual** step
    today. You deploy and configure a Kerbside proxy out of band (see the
    [Kerbside component documentation](/components/kerbside/)), then point
    Shaken Fist at it as below.

To enable it, set `KERBSIDE_URL` to your Kerbside deployment's public base
URL. You can do this at deploy time by setting the collection's
`kerbside_url` variable in your `group_vars/all.yml` (see
[Installation](installation.md)), which renders it into every node's
`/etc/sf/config`:

```yaml
kerbside_url: https://kerbside.example.com
```

or on a running cluster with `sf-ctl` (a `cluster_config` value overrides
the rendered file at process start):

```bash
sf-ctl set-config KERBSIDE_URL https://kerbside.example.com
```

`KERBSIDE_URL` is both the base for the returned console URL and the token
**audience** (`aud`) claim. Kerbside's expected audience — its own
`SF_CONSOLE_TOKEN_AUDIENCE`, or a value derived from its `PUBLIC_FQDN` if
that is unset — must equal this string exactly, or verification fails.

Optionally set `KERBSIDE_TOKEN_DURATION`, the lifetime in seconds of a
minted token. It defaults to 300 seconds (five minutes), which is ample for
a viewer to redeem the URL:

```bash
sf-ctl set-config KERBSIDE_TOKEN_DURATION 300
```

Both values are read from cluster configuration at `sf-api` start, so change
them before (or restart `sf-api` after) enabling the integration.

## Signing key custody

The private key that signs console tokens lives in a single
`cluster_config` row named `KERBSIDE_JWT_SIGNING_KEY`. Treat its custody the
same way you treat `AUTH_SECRET_SEED`: it is cluster-wide secret material,
and anyone who can read it can mint console tokens for any instance.

The stored value is a small JSON document holding the active key id and a
newest-first list of Ed25519 keypairs (private and public PEM). Because the
configuration name ends in `_KEY`, `sf-ctl show-config` redacts it by
default; never pass `--show-secrets` where the output could be logged, and
never print or event private key material.

The key is **not** created automatically: it must be provisioned explicitly
before the first console is opened. Until it exists, the `vdiconsoleproxy`
endpoint returns HTTP 500 naming the command to run below — minting never
self-bootstraps a key. Create it — for example so you can publish the public
key to Kerbside before anyone opens a console — with the idempotent:

```bash
sf-ctl ensure-kerbside-signing-key
```

This prints the active key id and how many keys are published; it never
prints private material and does nothing if a key already exists.

## Publishing the public keys

Kerbside verifies tokens with the **public** half of the signing key, which
Shaken Fist serves to admin callers at
[`GET /admin/vditokenpubkey`](https://openapi.shakenfist.com/#/admin/get_admin_vditokenpubkey).
The response is the active key id and every currently published public key:

```json
{
    "active_kid": "3f2a9c1e",
    "keys": [
        {
            "kid": "3f2a9c1e",
            "alg": "EdDSA",
            "public_pem": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----\n",
            "created": 1789000000
        }
    ]
}
```

It returns HTTP 404 until a signing key exists (run
`sf-ctl ensure-kerbside-signing-key` first). Kerbside fetches this endpoint
to learn the verification keys; a token is accepted if it is signed by
**any** published key, which is what makes rotation safe.

## Rotation runbook

Rotation publishes a fresh signing key while keeping the previous one
verifiable, so a rotation never invalidates tokens that are still in flight.
The stored key list is a **two-key window**: rotation prepends a new key,
marks it active, and trims the list to the two newest entries.

To rotate:

```bash
sf-ctl rotate-kerbside-signing-key
```

This prints the new active key id and the published key count (which is 2
after the first rotation). New tokens are signed only with the active key;
tokens signed by the previous key still verify because Kerbside accepts any
published key.

!!! warning

    Do not rotate more often than `KERBSIDE_TOKEN_DURATION`. Each rotation
    drops the third-oldest key from the two-key window, and any token signed
    by a dropped key immediately becomes unverifiable. Waiting at least one
    token lifetime between rotations guarantees every in-flight token was
    signed by a key that is still published. Rotating twice in quick
    succession can strand live sessions.

!!! note

    Run the signing-key commands (`ensure-kerbside-signing-key`,
    `rotate-kerbside-signing-key`) one at a time. Both perform an unlocked
    read-modify-write of the `KERBSIDE_JWT_SIGNING_KEY` cluster config, so two
    running concurrently could lose one write and strand a key that should
    still be published. These are manual, infrequent operator actions, so
    serialising them is trivial — just don't fire two at once.

A safe cadence is therefore: rotate, wait for Kerbside to refresh its
published keys and for at least `KERBSIDE_TOKEN_DURATION` to elapse, then
rotate again if needed.

## Host subject enforcement

Kerbside pins the backend it connects to by certificate subject, so a
misdirected or spoofed hypervisor address cannot silently receive a session.
The subject it enforces for a given node is the value Shaken Fist publishes
as that node's `spice_server_cert_subject` attribute.

Each hypervisor reads its own SPICE server certificate at node-observation
time and publishes the parsed subject (for example
`C=US,O=Shaken Fist,CN=hv1`). Nodes without a SPICE server certificate — a
non-hypervisor node, or a hypervisor whose certificate is missing or
unreadable — publish nothing, which leaves host-subject enforcement disabled
for that backend rather than breaking the node. Kerbside consumes this value
during its cluster-wide scrape and uses it as the connection `host_subject`.
See the
[Kerbside console sources page](/components/kerbside/console-sources/) for
the proxy-side view.

## Related pages

* [Consoles (user guide)](/user_guide/consoles/) — how a user opens a
  proxied console.
* [Installation](installation.md) — where `extra_config` and cluster
  variables are set.
* [Kerbside component documentation](/components/kerbside/) — the proxy,
  its console sources, and its own configuration.
* [`GET /admin/vditokenpubkey`](https://openapi.shakenfist.com/#/admin/get_admin_vditokenpubkey)
  and
  [`GET /instances/{instance_ref}/vdiconsoleproxy`](https://openapi.shakenfist.com/#/instances/get_instances__instance_ref__vdiconsoleproxy)
  in the API reference.
