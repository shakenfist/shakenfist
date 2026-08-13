# Console Sources

Kerbside can connect to the following platforms:

- [Shaken Fist](https://shakenfist.com)
- [oVirt](https://www.ovirt.org), an Open Source Red Hat supported
  virtualization system
- [OpenStack](https://www.openstack.org), an Open Source cloud compute platform

The connection to each platform (a source of consoles, so "console sources") is
defined in the `sources.yaml` configuration file in YAML format. The path to
this file is configured via the `SOURCES_PATH` setting.

## How Console Sources Work

Kerbside handles different source types in different ways:

- **oVirt**: These sources are queried regularly (once a minute) for a list of
  available consoles. The consoles are stored in the database and presented in
  the Kerbside administrative interface.

- **Shaken Fist**: Shaken Fist sources are also scraped regularly, but under a
  `system` credential the scrape covers the whole cluster rather than a single
  namespace. Access then goes through a token exchange rather than opening a
  console straight from the scraped table: Shaken Fist mints a short-lived,
  Ed25519-signed token that Kerbside verifies entirely offline at
  `/sf-console.vv`. See the [Shaken Fist](#shaken-fist) section below.

- **OpenStack**: OpenStack sources are handled differently. Rather than periodic
  scraping, OpenStack uses on-demand authentication. When a user requests a
  console via Nova's SPICE console API, Nova provides an authentication token
  that Kerbside validates against the configured OpenStack source. This means
  OpenStack consoles appear dynamically when requested rather than being
  pre-discovered.

It is possible to have more than one console source for a given type, so for
example the VDI proxy could be used to combine virtual machines from two
OpenStack clusters together seamlessly.

## Shaken Fist

Shaken Fist sources are periodically scraped for their available consoles, but
both the scrape and the console-access flow differ from the other scraped
source (oVirt), so they are described here in full.

**Cluster-wide scrape.** When the source's `username` is `system`, Kerbside
scrapes the *entire* cluster in one pass: every namespace's instances are
enumerated, so all of the cluster's SPICE consoles become reachable through a
single source. With any other `username`, only that namespace's instances are
scraped. Each pass also refreshes the cluster's node list so a console can be
associated with the hypervisor currently hosting it. An instance is only kept
when it is in the `created` state and its VDI video model is a SPICE variant.

**Token exchange.** Unlike oVirt, a Shaken Fist console is not opened directly
from the scraped table. Shaken Fist mints a short-lived, Ed25519-signed JWT and
hands the viewer an exchange URL of the form
`<KERBSIDE_URL>/sf-console.vv?token=<jwt>`. Kerbside verifies that JWT
*entirely offline* against the cluster's signing public keys, which it caches
when the source is initialized (and refetches exactly once if it sees an
unknown key id, so a signing-key rotation is tolerated). Verification checks
the signature, the audience (`aud`), and the expiry (`exp`), and enforces
single use by recording the token's `jti` so a replayed token is rejected — no
callback to Shaken Fist happens on this path. Only once the token verifies does
Kerbside look the scraped console up, confirm that the console's source matches
the source whose key verified the token, and issue the usual Kerbside console
token and virt-viewer (`.vv`) file. The audience Kerbside accepts is set by
`SF_CONSOLE_TOKEN_AUDIENCE` (or, when unset, derived from `PUBLIC_FQDN`) and
must equal Shaken Fist's `KERBSIDE_URL` exactly; see
[Configuration](/components/kerbside/configuration/).

**Backend certificate pinning.** At scrape time each console's `host_subject`
is pinned from the hypervisor node's published SPICE server certificate subject
(`spice_server_cert_subject`), so the proxy's backend TLS leg can verify it is
talking to the expected hypervisor. A node that publishes no subject (an older
cluster, or a node without a cert) leaves `host_subject` unset, and the proxy
skips host-subject enforcement for that backend rather than refusing it. The
optional `synthesize_host_subject` knob (below) lets an operator on the stock
`cn=hostname` PKI turn enforcement on before the node-side change is deployed.

The following options are used to configure a Shaken Fist console source
(`type: shakenfist`).

| Option | Description |
|--------|-------------|
| source | The name of the source (used as an identifier) |
| type | The type of the source: `shakenfist` |
| url | The API URL for the Shaken Fist cluster |
| username | The Shaken Fist namespace to authenticate to; use `system` to scrape the whole cluster |
| password | The API key/password to authenticate with |
| ca_cert | Required: the SSL CA public key certificate to validate API and VDI connections against |
| synthesize_host_subject | Optional (default false): when a node publishes no `spice_server_cert_subject`, synthesize `CN=<node>` so the proxy still enforces the backend host subject. Only correct on a stock `cn=hostname` PKI — a mismatch would wrongly reject the backend — so it is off by default. |

**Note**: The CA certificate is verified against the cluster's advertised
certificate during initialization, and the cluster's VDI token signing keys are
fetched at the same time. If the CA certificates do not match, or the signing
keys cannot be fetched, the source is marked as errored.

## oVirt

The following options are used to configure an oVirt console source
(`type: ovirt`).

| Option | Description |
|--------|-------------|
| source | The name of the source (used as an identifier) |
| type | The type of the source: `ovirt` |
| url | The oVirt Engine URL (e.g., `https://ovirt.example.org/ovirt-engine`). Must **not** end in `/api` -- Kerbside appends that itself |
| username | The username to authenticate to the source as. oVirt 4.4.7 and later name the built-in administrator `admin@ovirt@internalsso`; older deployments use `admin@internal` |
| password | The password to authenticate with |
| ca_cert | Required: the SSL CA public key certificate to validate API and VDI connections against. Inline PEM text, not a path |

**Note**: The CA certificate is verified against the engine's PKI certificate
during initialization. If they don't match, the source will be marked as
errored. A source that errors as soon as it is configured usually means the
pasted CA is stale or truncated, rather than that the engine is unreachable.

The engine URL must use the name the engine's certificate was issued for,
and that name must resolve from the Kerbside host: an IP address URL fails
certificate verification.

The account needs to list VMs, list hosts (Kerbside pins each VM's host
certificate subject), read graphics consoles, and acquire console tickets
-- the last requires the `RECONNECT_TO_VM` action group on the VMs. Listing
hosts is an administrative operation, so a plain VM-portal role is not
enough; only `SuperUser` has been tested.

Kerbside connects to the hypervisor's SPICE ports directly, so it needs L3
reachability to every hypervisor, and oVirt's own SPICE proxy
(`SpiceProxyDefault`) is not in the path. See
[Kerbside for oVirt](/components/kerbside/use-cases/ovirt/) for the deployment architecture,
the network prerequisites, and what is and is not proven.

## OpenStack

OpenStack sources work differently from Shaken Fist and oVirt. Instead of
periodically scraping for available consoles, Kerbside validates authentication
tokens issued by Nova when users request SPICE direct console access.

Nova 2025.1 (Epoxy) and later includes native support for SPICE direct consoles
via the "spice-direct" console type. When a user requests a console, Nova returns
a URL pointing to Kerbside with an authentication token. Kerbside validates this
token via Nova's `/os-console-auth-tokens/` API and establishes the proxied
connection to the hypervisor.

The following options are used to configure an OpenStack console source
(`type: openstack`).

| Option | Description |
|--------|-------------|
| source | The name of the source (used as an identifier) |
| type | The type of the source: `openstack` |
| url | The Keystone authentication URL (e.g., `http://keystone.example.org:5000`) |
| username | The username for the service account |
| password | The password for the service account |
| project_name | The OpenStack project name for the service account |
| user_domain_id | The OpenStack user domain ID (typically "default") |
| project_domain_id | The OpenStack project domain ID (typically "default") |
| ca_cert | Optional: the SSL CA public key certificate to validate connections against |

**Note**: OpenStack integration requires Nova 2025.1+ with SPICE direct console
support enabled. See the
[Kerbside Patches repository](https://github.com/shakenfist/kerbside-patches)
for Kolla-Ansible deployment support, and the
[Nova specification](https://specs.openstack.org/openstack/nova-specs/specs/2025.1/implemented/libvirt-spice-direct-consoles.html)
for configuration details.

## Static source

The static source driver (`type: static`) reads its VM-to-console
mapping entirely from an inline `consoles:` list in the sources.yaml
entry.  No external API calls are made and no control plane is needed.

**Intended use-cases:**

- **CI pipelines** that boot a QEMU guest directly and need kerbside
  to front it.  The direct-qemu CI workflow uses this driver.
- **Ad-hoc debugging** where you want to point kerbside at a hand-
  rolled QEMU without spinning up a full Shaken Fist or oVirt
  deployment.

**Not intended for production use.**  The console list is static —
kerbside must be restarted to pick up changes, there is no polling,
and there is no liveness check on the QEMU process behind the ticket.

The following options are used to configure a static console source
(`type: static`).

| Option | Description |
|--------|-------------|
| source | The name of the source (used as an identifier) |
| type | The type of the source: `static` |
| consoles | A list of console entry dicts (see fields below) |

Each entry in the `consoles` list requires the following fields:

| Field | Description |
|-------|-------------|
| uuid | Unique identifier for this console (must be globally unique) |
| name | Human-readable display name |
| hypervisor | Hostname of the hypervisor (used if hypervisor_ip is empty) |
| hypervisor_ip | IP address of the hypervisor |
| insecure_port | SPICE port (plaintext) |
| ticket | SPICE password / authentication ticket |

Optional fields (default to null):

| Field | Description |
|-------|-------------|
| secure_port | SPICE TLS port (if QEMU exposes one) |
| host_subject | TLS host subject for certificate verification. Enforced: when set, the proxy refuses hypervisors whose certificate subject does not match (exact attribute count/order/type). |

**Example sources.yaml entry for a static source:**

```yaml
- source: ci-direct-qemu
  type: static
  consoles:
    - uuid: "6f4e2c1a-0000-0000-0000-000000000001"
      name: "sextant-ci-vm"
      hypervisor: "localhost"
      hypervisor_ip: "127.0.0.1"
      insecure_port: 5910
      ticket: "my-spice-password"
```

A two-console example with inline comments is available at
`etc/example-static-sources.yaml`.

The static source pairs cleanly with
[Ryll's control socket](https://github.com/shakenfist/ryll/blob/main/docs/control-socket-protocol.md)
for end-to-end direct-QEMU testing. A test harness boots a QEMU
guest, declares it via the static source, points kerbside at that
source, and then drives the SPICE session through Ryll's
`--control-socket` interface — sending keystrokes, pasting text,
and capturing screenshots without a GUI. The control-socket protocol
is the bridge between the test driver and the SPICE session it
asserts against.

The direct-qemu lane's final step runs the Sextant scenario tempest
test (`tests/scenario/test_sextant_scenario.py`), which drives the
full Awaiting → Booting → bootloader-ignore → paste → Parked →
shutdown sequence and asserts both the live `digest_updated` QR
event stream and the post-mortem serial drain. The test skips when
`CONF.kerbside.control_socket_path` is unset, so it is safe to load
the plugin on the OpenStack lane where these direct-qemu options are
not configured. `tools/direct-qemu/run-scenario.sh` handles tempest
venv setup, config file generation, and test execution.

## Example sources.yaml

An example configuration follows:

```yaml
- source: sfmel
  type: shakenfist
  url: https://sfmel.example.org/api
  username: sfvdi
  password: ...omitted...
  ca_cert: |
    -----BEGIN CERTIFICATE-----
    ...
    -----END CERTIFICATE-----

- source: ovirt
  type: ovirt
  url: https://ovirt.example.org/ovirt-engine
  username: kerbside@internal
  password: ...omitted...
  ca_cert: |
    -----BEGIN CERTIFICATE-----
    ...
    -----END CERTIFICATE-----

- source: kolla
  type: openstack
  url: http://keystone.example.org:5000
  username: kerbside
  password: ...omitted...
  project_name: service
  user_domain_id: default
  project_domain_id: default
```

## Related Documentation

- [Configuration](/components/kerbside/configuration/) - General configuration reference
- [Proxy Architecture](/components/kerbside/proxy-architecture/) - Internal proxy design
