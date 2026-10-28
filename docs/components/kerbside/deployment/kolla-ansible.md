# Deploying Kerbside with Kolla-Ansible

This page deploys Kerbside as one of an OpenStack cloud's own
control-plane services, with a Kolla-Ansible that carries a
`kerbside` role. That role is not in upstream Kolla-Ansible. It lives
in [kerbside-patches](https://github.com/shakenfist/kerbside-patches),
which carries it as a patch against Kolla-Ansible master, kept
applying by rebase. The page also covers the other way to put
Kerbside in front of a Kolla-Ansible cloud: deploy Kerbside some other
way, and configure a stock Kolla-Ansible's Nova to point at it.

## Who this is for

An operator who already runs Kolla-Ansible, wants Kerbside as one
more control-plane service beside Nova, and is content to deploy
from a patched Kolla-Ansible and to build one image, Nova's
`nova-libvirt-spice`, which Kolla does not publish. The role then
does nearly everything: the containers, the database, the Keystone
account, the certificates, HAProxy, and the Nova setting that sends
`spice-direct` consoles to Kerbside.

It is not for an operator who wants one Kerbside in front of several
clouds, or in front of anything other than the cloud deploying it.
The role writes exactly one console source, the cloud it is
deploying into, and renders Kerbside's configuration from its own
templates with no override path, so there is nowhere to add a second.
[Multi-cloud aggregation](/components/kerbside/use-cases/multi-cloud/) describes that
shape. Deploy that Kerbside some other way, such as
[by hand](/components/kerbside/deployment/manual/), and configure each Kolla-Ansible cloud as
[A Kerbside in front of a stock Kolla-Ansible cloud](#a-kerbside-in-front-of-a-stock-kolla-ansible-cloud)
describes. The same section suits an operator who will not run a
patched Kolla-Ansible.

This page is about how Kerbside is deployed into a Kolla-Ansible
cloud, not about why. What Kerbside is worth in front of OpenStack,
how the `spice-direct` token exchange works, and the cloud-side
configuration any Kerbside in front of OpenStack needs (Nova's two
settings, the Keystone account, and the network) are in
[Kerbside for OpenStack](/components/kerbside/use-cases/openstack/#how-to-set-it-up).

## What it deploys

Everything in this section comes from the role as kerbside-patches
carries it, in
[`_patches/patch147-kolla-ansible-master-kolla-ansible-kerbside-role.patch`](https://github.com/shakenfist/kerbside-patches/blob/develop/_patches/patch147-kolla-ansible-master-kolla-ansible-kerbside-role.patch).
That patch is the same commit as the current patch set of
kolla-ansible change
[976889](https://review.opendev.org/c/openstack/kolla-ansible/+/976889),
which is where the role is under review. Setting `enable_kerbside`
in `globals.yml` turns it on.

| Piece | What the role does |
|-------|--------------------|
| Containers | `kerbside_api` runs the REST API (`kerbside.api:app`) under uWSGI, on the hosts in the `kerbside_api` group. `kerbside_proxy` runs `kerbside daemon run`, the daemon that discovers consoles and supervises the SPICE proxy as its child, on the hosts in the `kerbside_proxy` group. A one-shot `bootstrap_kerbside` container runs the database migrations. |
| Inventory | The shipped `all-in-one` and `multinode` inventories map `kerbside_api` to `control`, and `kerbside_proxy` to a new `vdiproxy` group which is itself `control`. |
| Images | `kerbside-api` and `kerbside-proxy`, built by Kolla. With `enable_kerbside` set, Nova's libvirt container also switches from `nova-libvirt` to Kolla's `nova-libvirt-spice` image. |
| Database | A `kerbside` database and user on the cloud's MariaDB, unless `use_preconfigured_databases` is set, and the schema, which the bootstrap container migrates. |
| Keystone | A `kerbside` user (`kerbside_keystone_user`) in the `service` project with the `admin` role, and a Keystone group named `kerbside`, with no members. |
| `kerbside.ini` | Rendered for each container. The service account for interactive login is the same `kerbside` user, in the `service` project, against the internal Keystone endpoint; `keystone_access_group` is `kerbside`; `public_fqdn` is Keystone's external name. |
| `sources.yaml` | One `type: openstack` source, named after the region, using the same `kerbside` account against the internal Keystone endpoint, with `interface: internal`. `verify` is Kolla's `openstack_cacert` where one is set, and `true` otherwise. When `nova_spice_require_secure` is set, the source's `ca_cert` is the libvirt CA, which is what the proxy verifies a hypervisor against when that hypervisor demands TLS. |
| Certificates | Kerbside's certificate, key and CA, as `kerbside-cert.pem`, `kerbside-key.pem` and `kerbside-ca.pem` in Kolla's certificates directory. `kolla-ansible certificates` generates them from Kolla's own root CA when `certificates_generate_kerbside` is set, which it is by default whenever `enable_kerbside` is. The generated certificate names both the internal and the external VIP. |
| `nova.conf` | `[spice] spice_direct_proxy_base_url` in the `nova-cell` services' `nova.conf`, set to `kerbside_spice_direct_proxy_base_url`. That defaults to `/nova-console.vv` on the external FQDN and the API's port, over `https` when Kolla's external TLS is on. A second patch in the same series, kolla-ansible change [967801](https://review.opendev.org/c/openstack/kolla-ansible/+/967801), writes `server_proxyclient_address` whether or not the HTML5 console proxy is enabled. |
| HAProxy | The API on port 13002 in HTTP mode, on the internal and external VIPs, health-checked with `GET /`. The SPICE proxy on 5898 (TLS) and 5899 (plaintext) in TCP mode, on both VIPs, with a one-hour tunnel timeout. HAProxy does not terminate the SPICE TLS; the proxy does, with Kerbside's certificate. |
| uWSGI | The API is served by uWSGI. With `kerbside_enable_tls_backend`, which defaults to on, uWSGI serves it over TLS with Kerbside's certificate, behind HAProxy. |
| Logging | Fluentd tails `/var/log/kolla/kerbside/*.log` as JSON, and the uWSGI log separately. The cron container rotates them. |
| Odds and ends | Three new entries in `passwords.yml` (the database and Keystone passwords and Kerbside's `auth_secret_seed`), and `KERBSIDE_API_URL` in `admin-openrc.sh`. |

The role's SPICE ports, 5898 and 5899, are not Kerbside's own
defaults of 5900 and 5901, which hypervisor consoles use too;
[network-ports.md](/components/kerbside/network-ports/#kerbsides-defaults-versus-a-hypervisors)
says why that matters on a host that runs both, as an all-in-one
deployment does.

What it leaves to you:

- **A patched Kolla-Ansible, and one image.** The role exists only
  in kerbside-patches. Kolla publishes Kerbside's own images,
  `kerbside-api` and `kerbside-proxy`, on quay.io, built from
  Kerbside's `develop` branch
  ([Kerbside in containers](/components/kerbside/deployment/containers/#what-it-deploys) has the
  detail). It does not publish `nova-libvirt-spice`, which the role
  switches Nova to, so that image has to be built. kerbside-patches'
  [Building patched container images](https://github.com/shakenfist/kerbside-patches/blob/develop/docs/building.md)
  describes how its CI builds it.
- **`nova_console: "spice"`.** The role's precheck fails unless Nova
  is enabled with SPICE consoles. It does not set this for you.
- **Who may log in.** The role creates the `kerbside` group and adds
  nobody to it. Add the Keystone users who may use Kerbside's web UI
  and REST API.
- **Kerbside administrators.** The role does not set
  `keystone_admin_group`, and `kerbside.ini` has no override path, so
  nobody is a Kerbside administrator and nobody can fetch a direct
  console file (see
  [configuration.md](/components/kerbside/configuration/#keystone-settings)).
- **Certificates for production.** The generated ones are signed by
  Kolla's own root CA, which suits a test deployment. Put your own
  three files in Kolla's certificates directory instead.
- **The firewall.** SPICE clients need to reach the external VIP on
  13002, 5898 and 5899.
- **Guests.** Their SPICE display and agents, as
  [Kerbside for OpenStack](/components/kerbside/use-cases/openstack/#how-to-set-it-up)
  describes.

## How to use it

Follow kerbside-patches. Its
[Building patched container images](https://github.com/shakenfist/kerbside-patches/blob/develop/docs/building.md)
covers building the images and installing the patched Kolla-Ansible,
in its section "To actually deploy Kolla-Ansible". The settings
that matter for Kerbside in `globals.yml` are
`kolla_base_distro: "debian"`, `nova_console: "spice"` and
`enable_kerbside: "yes"`. The role also carries its own operator guide,
`doc/source/reference/compute/kerbside-guide.rst`, inside the same
patch, which covers the URL Nova advertises and the certificates.
Kolla-Ansible's own documentation covers everything that is not
Kerbside's.

**Debian container images.** kerbside-patches' README gives Debian
as the only tested container operating system. RHEL 9 dropped SPICE
from its KVM, the redistributions such as Rocky followed, and
Kolla-Ansible will not mix container operating systems, so the README
asks for every container in the deployment to be Debian, not just
Nova's. Two things have moved since it was written. Kolla has merged
[986283](https://review.opendev.org/c/openstack/kolla/+/986283),
which builds `nova-libvirt-spice` on Rocky from a third-party COPR
rebuild of `qemu-kvm`, and which Kolla's release note recommends for
proofs of concept only. And kerbside-patches' own CI now has weekly
lanes, beside its Debian ones, that deploy Ubuntu and Rocky 10
container images. Kerbside's own lane deploys Debian images only, so
Debian is still the choice this page can stand behind.

### Upstream status

The Kolla image build for Kerbside is upstream, and so are the SPICE
options the role depends on. The deployment is not. Checked on
**2026-10-28**:

| Change | What it does | Status |
|--------|--------------|--------|
| [kolla 975495](https://review.opendev.org/c/openstack/kolla/+/975495) | Builds the Kerbside container images | Merged |
| [kolla-ansible 967800](https://review.opendev.org/c/openstack/kolla-ansible/+/967800) | Adds the SPICE configuration options | Merged |
| [kolla-ansible 967802](https://review.opendev.org/c/openstack/kolla-ansible/+/967802) | Allows requiring secure SPICE channels, which is where `nova_spice_require_secure` comes from | Merged |
| [kolla-ansible 976889](https://review.opendev.org/c/openstack/kolla-ansible/+/976889) | Deploys Kerbside with Kolla-Ansible | Open |
| [kolla-ansible 967801](https://review.opendev.org/c/openstack/kolla-ansible/+/967801) | Always configures a routable console address, which `spice-direct` needs and only the HTML5 path used to get | Open |
| [kolla-ansible 988189](https://review.opendev.org/c/openstack/kolla-ansible/+/988189) | Adds the Kerbside CI scenario jobs | Open |
| [kolla-ansible 988913](https://review.opendev.org/c/openstack/kolla-ansible/+/988913) | Runs Nova's upstream `spice-direct` Tempest test in those jobs | Open |
| [kolla-ansible 989614](https://review.opendev.org/c/openstack/kolla-ansible/+/989614) | Runs Kerbside's own Tempest test in those jobs | Open |

The
[`spice-direct-consoles` topic on the OpenStack Gerrit](https://review.opendev.org/q/topic:spice-direct-consoles)
is the live answer if this snapshot has aged.

The Kolla-Ansible deployment is maintained by rebase. kerbside-patches
keeps it applying to Kolla-Ansible master, and Kerbside's own
OpenStack CI lane deploys from it. The changes stay open in Gerrit,
and a rebase refreshes them there, but nobody is pressing for their
review.

The intent is to keep pushing only the part of the series that any
Kerbside in front of OpenStack needs from Kolla-Ansible, however
Kerbside is deployed: the routable console address in 967801, and a
Keystone account for Kerbside that does not depend on Kolla deploying
Kerbside. As the changes stand, that has not taken effect. 967801 is
stacked on 976889 in Gerrit, so it cannot merge before the deployment
change unless it is restacked onto master, and no change for the
account exists yet.

### A Kerbside in front of a stock Kolla-Ansible cloud

A stock Kolla-Ansible cannot deploy Kerbside, but it can run the
cloud a Kerbside fronts. Deploy Kerbside some other way, such as
[by hand](/components/kerbside/deployment/manual/), then do three things. No CI lane runs this
path.

**SPICE consoles.** Set `nova_console: "spice"` in `globals.yml`.
Kolla-Ansible then writes `[vnc] enabled = false` and `[spice]
enabled = true` into the `nova.conf` of the `nova-cell` services, and
has QEMU listen for SPICE on each compute host's `api_interface`
address. Nova adds the SPICE display to an instance when it builds
the instance's libvirt domain, so an instance started while the cloud
used VNC has no SPICE console until Nova rebuilds its domain, for
example at a hard reboot. On Debian and Ubuntu, Kolla builds the
stock `nova-libvirt` image the same way as `nova-libvirt-spice`, the
image the Kerbside role uses. On Rocky it does not: Rocky's own
`qemu-kvm` has SPICE compiled out.

**Nova's two settings.** `spice_direct_proxy_base_url` and
`server_proxyclient_address`, both in `[spice]`, are read only by
`nova-compute`: the compute manager builds the console URL from the
first, and the libvirt driver reports the second as the address to
dial. So both have to reach `nova-compute`'s `nova.conf` on every
compute host. What they mean, and what Kerbside needs of each, is in
[Kerbside for OpenStack](/components/kerbside/use-cases/openstack/#how-to-set-it-up).

- Stock Kolla-Ansible never writes `spice_direct_proxy_base_url`,
  and Nova's default, `http://127.0.0.1:13002/nova`, is not a
  Kerbside URL. Nova appends `?token=` and the token to the value as
  it stands, so it must be the full URL of Kerbside's exchange
  endpoint, ending in `/nova-console.vv`.
- Stock Kolla-Ansible writes `server_proxyclient_address`, as each
  host's `api_interface` address, only while the HTML5 SPICE proxy is
  enabled (`nova_spice_html5`, on by default). That is the gap 967801
  closes. If you keep the HTML5 proxy, leave it alone. If you turn
  the proxy off, set it yourself, or Nova reports its default,
  `127.0.0.1`, and Kerbside dials itself.

Kolla-Ansible merges `nova.conf` for the `nova-cell` services from
these files, in this order, with a later file's value for an option
replacing an earlier one's:

1. Kolla-Ansible's own `nova.conf.j2` template;
2. `/etc/kolla/config/global.conf`;
3. `/etc/kolla/config/nova.conf`;
4. `/etc/kolla/config/nova/<service>.conf`;
5. `/etc/kolla/config/nova/<hostname>/nova.conf`;
6. `/etc/kolla/config/nova/<hostname>/<service>.conf`.

`/etc/kolla/config/nova/nova-compute.conf` reaches exactly the
service that reads both options, on every compute host. Kolla-Ansible
renders override files as Jinja templates with each host's variables,
so one file can give every host its own address:

```ini
[spice]
spice_direct_proxy_base_url = https://kerbside.example.com:13002/nova-console.vv
server_proxyclient_address = {{ api_interface_address }}
```

Use the name and port your Kerbside's API is reached on, and drop
the second line if the HTML5 proxy stays on.
`{{ api_interface_address }}` is the address Kolla-Ansible already
has QEMU listen on, so it is right as long as Kerbside can route to
each compute host's API network. Run `kolla-ansible reconfigure`, then read
`/etc/kolla/nova-compute/nova.conf` on a compute host to check that
both settings arrived: Kolla-Ansible ignores an override file in a
path it does not look in, without a warning.

**The Keystone account and the network.** Create the account Kerbside
validates console tokens with, configure Kerbside's source for the
cloud, and open the paths between Kerbside, the compute hosts and
your users, all as
[Kerbside for OpenStack](/components/kerbside/use-cases/openstack/#how-to-set-it-up)
describes. Kerbside reaches Nova's internal endpoint by default,
which suits a Kerbside on the cloud's management network.

## Status and limitations

Kerbside is [experimental](/components/kerbside/index/#kerbside-is-experimental)
overall. Kerbside's `openstack_matrix` CI lane deploys through this
role every night, from kerbside-patches with the Kerbside being
tested, using kerbside-patches' `etc/globals-master.yml`: HAProxy,
internal TLS and libvirt TLS on, `nova_spice_require_secure` on, the
HTML5 SPICE proxy off, and fluentd off.
[A worked example](/components/kerbside/use-cases/openstack/#a-worked-example)
describes what that lane proves.

| Not covered | Why |
|-------------|-----|
| Stock Kolla-Ansible cannot deploy Kerbside | The image build has merged, but the deployment, kolla-ansible change 976889, has not, and is maintained by rebase rather than pushed. kerbside-patches is the only route to a Kolla-deployed Kerbside. See [Upstream status](#upstream-status). |
| Nightly and all-in-one only | `openstack_matrix` is nightly-tier: it never runs on a pull request, so a regression shows up the morning after it merges ([testing.md](/components/kerbside/testing/#ci-tiers)). It deploys one all-in-one host. The shipped `multinode` inventory puts a Kerbside on every control host, all sharing one database, and Kerbside's CI runs no such shape; kerbside-patches has a weekly two-node lane. [Placement topologies](/components/kerbside/use-cases/placement/) describes several Kerbsides sharing a database. |
| The account has `admin` | The role grants its `kerbside` user the `admin` role in the `service` project, because Nova's policy lets only an administrator validate another user's console token. No narrower role has been built or tried. |
| Debian images only, in Kerbside's CI | Kerbside's lane deploys Debian container images, and kerbside-patches' README names Debian as the only tested container operating system. Ubuntu and Rocky 10 images run only in kerbside-patches' weekly lanes, and Kolla's Rocky `nova-libvirt-spice` depends on a third-party rebuild of `qemu-kvm`. |
| One cloud per Kerbside | The role writes a single OpenStack source, the cloud it deploys into. A Kerbside fronting several clouds has to be deployed some other way. |
| Kerbside's configuration is not overridable | `kerbside.ini` and `sources.yaml` are rendered from the role's templates, with no `/etc/kolla/config` path, so a setting the role does not template, such as `keystone_admin_group`, cannot be set without changing the role. |
| Fluentd is untested here | Kerbside's lane runs with fluentd off, so nothing in Kerbside's CI exercises the role's log collection. |
| The stock Kolla-Ansible front door is untested | No lane deploys Kerbside outside Kolla-Ansible and points a stock Kolla-Ansible cloud at it, so the configuration in [A Kerbside in front of a stock Kolla-Ansible cloud](#a-kerbside-in-front-of-a-stock-kolla-ansible-cloud) has been checked against Kolla-Ansible's and Nova's source, not run. |
