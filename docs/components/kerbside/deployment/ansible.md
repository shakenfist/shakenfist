# Deploying Kerbside with Ansible

This page deploys Kerbside with the `kerbside` role of Kerbside's own
`shakenfist.kerbside` Ansible collection, which lives in this
repository under `ansible/` and is published to Ansible Galaxy with
each release. The role installs Kerbside into a virtual environment,
writes its configuration and two systemd units, migrates its database
and waits for it to be ready, on hosts you choose.

Two documents already describe the role in full, and this page links
them rather than repeating them. The collection's
[README](https://github.com/shakenfist/kerbside/blob/develop/ansible/README.md)
is the role's reference: its entry points, every variable, the ports,
certificates and redeploys. It ships inside the collection, so it is
also what Galaxy shows. Shaken Fist's
[Running Kerbside beside a cluster](https://shakenfist.com/operator_guide/kerbside/)
says where to put Kerbside next to a Shaken Fist cluster, what the
network needs, and how to load balance it.

## Who this is for

An operator who runs Ansible, whether or not they also run Shaken
Fist. With Shaken Fist, its own deploy runs the role for you. Without
it, the role is one more role in your playbook, and the Kerbside it
deploys can sit in front of whichever sources the role can deploy
today, which
[Status and limitations](#status-and-limitations) lists.

An operator who will not run Ansible should read
[By hand](/components/kerbside/deployment/manual/) instead. It writes out the same layout as
commands, and says wherever it differs from the role.

This page is about how Kerbside is deployed, not about why. For what
Kerbside is worth in front of a particular cloud, start with the
[use cases](/components/kerbside/index/#use-cases). The pieces any running Kerbside
needs are described once, in
[What a running Kerbside needs](/components/kerbside/installation/#what-a-running-kerbside-needs).

## What it deploys

Each row is what the role's tasks do, in
[ansible/roles/kerbside/tasks/](https://github.com/shakenfist/kerbside/tree/develop/ansible/roles/kerbside/tasks).

| Piece | What the role does |
|-------|--------------------|
| OS packages | Installs Kerbside's build dependencies with apt, so the hosts must be Debian family. |
| User | A `kerbside` system user and group, with no login shell. Its home, `/srv/kerbside`, is owned by root. |
| Virtual environment | `/srv/kerbside/venv`, holding Kerbside, `kerbside-proxy`, gunicorn and, only when a source is of type `shakenfist`, `shakenfist-client`. It installs with `uv pip install --reinstall`, so a rebuilt wheel with an unchanged version is picked up. |
| `kerbside.ini` | Rendered from the role's template, with the fifteen settings listed in [Settings the role does not write](#settings-the-role-does-not-write) and no others, and checked before it replaces the running file. `root:kerbside`, 0640. |
| `sources.yaml` | Your sources, written as given, and checked by Kerbside's rules for each type before it replaces the running file. `root:kerbside`, 0640. |
| Certificates | Nothing is issued. The role reads the proxy's certificate, key and CA from `/etc/kerbside/pki`, or from paths you name, and fails if any of the three is missing. It computes the proxy's certificate subject from the certificate itself. |
| systemd units | `kerbside-api.service`, gunicorn on `0.0.0.0:13002` in plain HTTP, and `kerbside-daemon.service`, the daemon with the SPICE proxy as its child. Both run as `kerbside` and are wanted by `multi-user.target`. |
| Database schema | `kerbside db upgrade`, once, on the first host, as the `kerbside` user. It checks that the migration read `kerbside.ini` and reached the database. |
| Restarts | Only when the installed code, the configuration it writes, the units, or the proxy's certificate, its key or the CA changed since the last deploy that reached readiness. |
| Readiness | Waits for the API to answer `GET /`, and for every `shakenfist` source, for Kerbside to fetch that cluster's console token signing keys during this deploy. |

The [By hand](/components/kerbside/deployment/manual/#what-it-deploys) page gives the same layout
as a table of paths, owners and modes, with the reasons for each.

What it leaves to you:

- **The database.** A MySQL or MariaDB server, and on it a `kerbside`
  database and a user with all privileges on it. The role migrates
  the schema and nothing more.
- **The proxy's certificate.** Its certificate, key and CA, in place
  before the role writes the configuration. Shaken Fist's deploy
  issues them from its own CA; anywhere else, they come from whatever
  issues certificates for you.
- **TLS in front of the API.** The role binds the API to every
  address in plain HTTP. The API carries bearer tokens and console
  files, so put a TLS-terminating reverse proxy in front of it, and
  set `kerbside_api_address` to `127.0.0.1` so that the proxy is the
  only way in. If the proxy is on another host, firewall the API port
  (`kerbside_api_port`, 13002 by default) to it instead. Kerbside
  ships no proxy configuration.
- **Client libraries for other sources.** The role installs
  `shakenfist-client` and no other client library
  ([#602](https://github.com/shakenfist/kerbside/issues/602)).
- **Any setting the role does not write.** See
  [Settings the role does not write](#settings-the-role-does-not-write).
- **The firewall.** [network-ports.md](/components/kerbside/network-ports/) lists
  every port Kerbside listens on and dials.

## How to use it

There are three routes, and each is described in full by the
document that owns it.

**Shaken Fist deploys Kerbside.** Add hosts to a `kerbside` group in
your Shaken Fist inventory, and Shaken Fist's `site.yml` runs the role
on them, with this cluster's source built for you. A host may be a
hypervisor or a host of its own. Shaken Fist's
[Topologies](https://shakenfist.com/operator_guide/kerbside/#topologies)
says which to choose and what each needs.

**Your own playbook.** For a Kerbside that is the front door to
several clouds, the shape [multi-cloud aggregation](/components/kerbside/use-cases/multi-cloud/)
describes, call the role from a playbook of your own. The README's
[Entry points](https://github.com/shakenfist/kerbside/blob/develop/ansible/README.md#entry-points)
gives a minimal playbook. Run the `validate` entry point as a play of
its own, before the deploy, so that a configuration which cannot work
is refused before any host changes. Your sources go in as a list, in
the form [console-sources.md](/components/kerbside/console-sources/) describes, and
the README's
[Console sources](https://github.com/shakenfist/kerbside/blob/develop/ansible/README.md#console-sources)
has a `shakenfist`, a `proxmox` and a `static` example.

**A Shaken Fist cluster joining a Kerbside you deploy.** Deploy
Kerbside by the second route, with a `shakenfist` source for the
cluster, and configure the cluster as Shaken Fist's
[Bring your own](https://shakenfist.com/operator_guide/kerbside/#bring-your-own)
describes. The cluster's `KERBSIDE_URL` and the role's
`kerbside_url` must be the same string, byte for byte, and `validate`
refuses a `shakenfist` source without one.

### Settings the role does not write

The role renders `kerbside.ini` from a fixed set of fifteen settings,
and nothing else:

- `sql_url`, `auth_secret_seed` and `sources_path`;
- `public_fqdn` and `node_name`, the host's inventory name;
- `cacert_path`, `proxy_host_cert_path`, `proxy_host_cert_key_path`
  and `proxy_host_subject`;
- `sf_console_token_audience`;
- `public_secure_port`, `public_insecure_port`, `vdi_secure_port`,
  `vdi_insecure_port` and `prometheus_metrics_port`.

The file says that local changes are overwritten on the next deploy,
and they are. So a role-deployed Kerbside cannot be given a Keystone
login, `firewall_mode`, `log_verbose`, its token durations,
`prometheus_metrics_address` or any other setting in
[configuration.md](/components/kerbside/configuration/) through `kerbside.ini`.
[#629](https://github.com/shakenfist/kerbside/issues/629) tracks
adding them to the role.

Until then, an environment variable works. Kerbside copies each key
of `kerbside.ini` into an environment variable named `KERBSIDE_` plus
the key in upper case, but only when that variable is not already
set, so a variable in the service's environment wins over the file.
`keystone_auth_url` becomes `KERBSIDE_KEYSTONE_AUTH_URL`. A systemd
drop-in can set such variables, and the role writes the unit files
themselves but not their drop-in directories, so a redeploy keeps it.

Put the settings in a file only root can read:

```bash
install -o root -g root -m 0600 /dev/null /etc/kerbside/kerbside.env
```

For a Keystone login, that file holds the `keystone_*` settings
[configuration.md](/components/kerbside/configuration/#keystone-settings) describes:

```ini
KERBSIDE_KEYSTONE_AUTH_URL=https://keystone.example.com:5000/v3
KERBSIDE_KEYSTONE_SERVICE_AUTH_USER=kerbside
KERBSIDE_KEYSTONE_SERVICE_AUTH_PASSWORD=CHANGEME
KERBSIDE_KEYSTONE_SERVICE_AUTH_PROJECT=service
KERBSIDE_KEYSTONE_SERVICE_AUTH_USER_DOMAIN_ID=default
KERBSIDE_KEYSTONE_SERVICE_AUTH_PROJECT_DOMAIN_ID=default
KERBSIDE_KEYSTONE_ACCESS_GROUP=kerbside
KERBSIDE_KEYSTONE_ADMIN_GROUP=kerbside-admins
# For a Keystone whose certificate a private CA issued:
# KERBSIDE_KEYSTONE_AUTH_VERIFY=/etc/kerbside/keystone-ca.pem
```

Then give both units a drop-in that reads it, as
`/etc/systemd/system/kerbside-api.service.d/settings.conf` and
`/etc/systemd/system/kerbside-daemon.service.d/settings.conf`:

```ini
[Service]
EnvironmentFile=/etc/kerbside/kerbside.env
```

systemd reads the file as root before it starts the service as
`kerbside`, so the file can stay 0600. Use `EnvironmentFile=` rather
than writing the values into the drop-in: `systemctl show` prints a
unit's own environment settings to any local user, and the service
password would be among them.

Apply it by hand, because the role will not:

```bash
systemctl daemon-reload
systemctl restart kerbside-daemon kerbside-api
```

The role decides whether to restart Kerbside from what it deploys
itself, which does not include a drop-in or the file it names, so a
change to either takes effect only when you restart the units.
Restarting the daemon ends every live SPICE session on the host.

Three more things to know:

- Use this only for settings the role does not write. The role's own
  database migration and readiness check run outside the units, so
  they would not see a variable that overrides one of the fifteen.
- A Keystone login also needs `keystoneauth1` and
  `python-keystoneclient` in the virtual environment
  ([Client libraries for console sources](/components/kerbside/installation/#client-libraries-for-console-sources)),
  which the role does not install
  ([#602](https://github.com/shakenfist/kerbside/issues/602)). Install
  them into it yourself, as root:
  `VIRTUAL_ENV=/srv/kerbside/venv /srv/kerbside/venv/bin/uv pip install keystoneauth1 python-keystoneclient`.
  The role's install names its own packages and removes nothing else,
  so a redeploy keeps them. Its restart decision hashes the
  virtual environment's package list, so the next deploy restarts
  Kerbside once.
- Nothing tests any of this. No CI lane deploys a drop-in, and the
  steps above are read from Kerbside's configuration loader and the
  role's tasks, not run.

## Status and limitations

Kerbside is [experimental](/components/kerbside/index/#kerbside-is-experimental)
overall. The sanity job runs the role's `validate`, `bootstrap`,
`config` and `register` entry points without root
(`tools/ci-test-ansible-role.sh`), with `shakenfist`, `ovirt`,
`openstack` and `static` sources, a stub
virtual environment, and no migration or units. It also lints, builds
and import-checks the collection. [`sf-e2e`](/components/kerbside/testing/#the-shaken-fist-end-to-end-lane-sf-e2e)
deploys each pull request's Kerbside through the role, on a Shaken
Fist node, and checks that a second deploy restarts nothing.

| Not covered | Why |
|-------------|-----|
| oVirt and OpenStack sources do not work | The role validates and renders all five source types, but installs no client library but `shakenfist-client` ([#602](https://github.com/shakenfist/kerbside/issues/602)). An `ovirt` source is marked errored and ignored, and an `openstack` source's token exchange answers 500. Today the role deploys a Kerbside for Shaken Fist, Proxmox and static sources, which need no other client library. |
| Proxmox through the role is untested | The role validates a `proxmox` source, and a unit test checks its required fields, but the rootless test renders none and no lane starts one. No CI lane yet proves a proxied session against a real Proxmox VE cluster, by any deployment mechanism. |
| No setting beyond the fifteen | `kerbside.ini` is rendered from a fixed set, with no way to add to it, so there is no Keystone login, firewall mode or verbose logging through the role ([#629](https://github.com/shakenfist/kerbside/issues/629)). The drop-in in [Settings the role does not write](#settings-the-role-does-not-write) works around it, and is untested. |
| Kerbside is started only with a Shaken Fist source, co-located | `sf-e2e` is the only lane that runs `register` against a real database and systemd, and it deploys one `shakenfist` source on a Shaken Fist node. The rootless test renders every source type but `proxmox`, and skips the migration and the units. No lane starts a role-deployed Kerbside with a `static` source, or on a host of its own. |
| Only through Shaken Fist's `site.yml` | `sf-e2e` calls the role from Shaken Fist's playbook. No lane calls it from a playbook of its own, as the second route above does. |
| One Kerbside host | No lane runs two Kerbside hosts sharing a database. Shaken Fist's [Load balancing](https://shakenfist.com/operator_guide/kerbside/#load-balancing) describes the shape, and says it is untested. |
| The API is plain HTTP | The role binds gunicorn to every address with no TLS, and no lane puts a reverse proxy in front of it. Until something binds it to loopback or firewalls it, port 13002 serves tokens and credentials in the clear. |
| A redeploy ends live sessions | A redeploy that changes Kerbside restarts it on every host in the same task, dropping every session at once, unless the play runs with `serial`. |
| Nobody can log in without Keystone | Interactive login is Keystone-only ([#300](https://github.com/shakenfist/kerbside/issues/300)), and the role configures no Keystone. Without one, people cannot log into the web UI, and something else has to hold credentials and call the API. |
