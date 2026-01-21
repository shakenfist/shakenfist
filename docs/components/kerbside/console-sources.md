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

- [Configuration](/components/kerbside/configuration.md) - General configuration reference
- [Proxy Architecture](/components/kerbside/proxy-architecture.md) - Internal proxy design
