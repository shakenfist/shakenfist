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

- **Shaken Fist and oVirt**: These sources are queried regularly (once a minute)
  for a list of available consoles. The consoles are stored in the database and
  presented in the Kerbside administrative interface.

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

The following options are used to configure a Shaken Fist console source
(`type: shakenfist`).

| Option | Description |
|--------|-------------|
| source | The name of the source (used as an identifier) |
| type | The type of the source: `shakenfist` |
| url | The API URL for the Shaken Fist cluster |
| username | The Shaken Fist namespace to authenticate to |
| password | The API key/password to authenticate with |
| ca_cert | Required: the SSL CA public key certificate to validate API and VDI connections against |

**Note**: The CA certificate is verified against the cluster's advertised
certificate during initialization. If they don't match, the source will be
marked as errored.

## oVirt

The following options are used to configure an oVirt console source
(`type: ovirt`).

| Option | Description |
|--------|-------------|
| source | The name of the source (used as an identifier) |
| type | The type of the source: `ovirt` |
| url | The oVirt Engine URL (e.g., `https://ovirt.example.org/ovirt-engine`) |
| username | The username to authenticate to the source as (e.g., `admin@internal`) |
| password | The password to authenticate with |
| ca_cert | Required: the SSL CA public key certificate to validate API and VDI connections against |

**Note**: The CA certificate is verified against the engine's PKI certificate
during initialization. If they don't match, the source will be marked as
errored.

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
  to front it.  The phase 5 direct-qemu CI workflow uses this driver.
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
asserts against. See phase 3 of the test-harness plan for the full
design:
`docs/plans/PLAN-test-harness-phase-03-control-socket.md`.

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
