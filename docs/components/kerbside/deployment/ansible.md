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
| Virtual environment | `/srv/kerbside/venv`, holding Kerbside, `kerbside-proxy`, gunicorn and the client library each configured source type's driver imports -- `shakenfist-client` for a `shakenfist` source, `ovirt-engine-sdk-python` for an `ovirt` one, `openstacksdk`, `keystoneauth1` and `python-keystoneclient` for an `openstack` one, and nothing for `proxmox` or `static`. It installs with `uv pip install --reinstall`, so a rebuilt wheel with an unchanged version is picked up. The deploy fails if a client did not install, rather than leaving a source to be marked errored later. |
| `kerbside.ini` | Rendered from the role's template: the fifteen settings in [Settings the role writes](#settings-the-role-writes), then whatever `kerbside_extra_settings` adds, and checked before it replaces the running file. `root:kerbside`, 0640. |
| `sources.yaml` | Your sources, written as given, and checked by Kerbside's rules for each type before it replaces the running file. `root:kerbside`, 0640. |
| Certificates | Nothing is issued. The role reads the proxy's certificate, key and CA from `/etc/kerbside/pki`, or from paths you name, and fails if any of the three is missing. It computes the proxy's certificate subject from the certificate itself. |
| systemd units | `kerbside-api.service`, gunicorn on `0.0.0.0:13002` in plain HTTP, and `kerbside-daemon.service`, the daemon with the SPICE proxy as its child. Both run as `kerbside` and are wanted by `multi-user.target`. |
| Database schema | `kerbside db upgrade`, once, on the first host, as the `kerbside` user. It checks that the migration read `kerbside.ini` and reached the database. |
| Restarts | Only when the installed code, the configuration it writes, the units, or the proxy's certificate, its key or the CA changed since the last deploy that reached readiness. |
| Readiness | Waits for the API to answer `GET /`, and for every `shakenfist` source, for Kerbside to fetch that cluster's console token signing keys during this deploy -- then checks that each fetched set could verify a token minted now, which a fetch alone does not prove. |

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

### Settings the role writes

The role writes these fifteen settings into `kerbside.ini` from its
own variables:

- `sql_url`, `auth_secret_seed` and `sources_path`;
- `public_fqdn` and `node_name`, the host's inventory name;
- `cacert_path`, `proxy_host_cert_path`, `proxy_host_cert_key_path`
  and `proxy_host_subject`;
- `sf_console_token_audience`;
- `public_secure_port`, `public_insecure_port`, `vdi_secure_port`,
  `vdi_insecure_port` and `prometheus_metrics_port`.

Each comes from a role variable the rest of the role reads too — the
ports it opens, the certificate it renders a subject from, the
audience it requires on a Shaken Fist console token — so `validate`
refuses any attempt to set one of these another way.

Every other setting in [configuration.md](/components/kerbside/configuration/) goes
in `kerbside_extra_settings`, a mapping of INI key to value rendered
after those fifteen:

```yaml
kerbside_extra_settings:
  log_verbose: true
  console_token_duration: 120
  firewall_mode: enforcing
  keystone_auth_url: https://keystone.example.com:5000/v3
  keystone_service_auth_user: kerbside
  keystone_service_auth_password: "{{ vault_kerbside_keystone_password }}"
  keystone_service_auth_project: service
  keystone_access_group: kerbside
  keystone_admin_group: kerbside-admins
```

The values are `no_log`, so none of them reaches the ansible output,
and `kerbside.ini` is `root:kerbside` 0640 as always. `no_log` redacts
a value wherever it appears, so a short one — `true`, a port, a
duration — can make an unrelated line of ansible's output read
`VALUE_SPECIFIED_IN_NO_LOG_PARAMETER`. That is cosmetic, and the
alternative is printing a service password. A name Kerbside has no
setting for is refused before the rendered file replaces the one
Kerbside is running with, because Kerbside ignores an unknown setting
rather than refusing it: a misspelled name would otherwise do nothing,
and say nothing.

Each value is written as one INI line, so it must be one line of
text, a number or a boolean. `validate` refuses a null (usually a
vault variable which did not resolve), a list or a mapping (which
would be written as its Python repr), and text with a line break in
it (which would be written as more than one line). A setting which
takes several values, such as `firewall_permitted_channels`, takes
them as one line, the way [configuration.md](/components/kerbside/configuration/)
spells it.

`kerbside.ini` is one of the files whose contents decide whether
Kerbside restarts, so changing a setting here restarts Kerbside on the
next deploy — which ends every live SPICE session on that host.

A Keystone login is any `keystone_*` setting, and the role installs
`keystoneauth1` and `python-keystoneclient` when it sees one
([Client libraries for console sources](/components/kerbside/installation/#client-libraries-for-console-sources)).
Interactive login is Keystone-only
([#300](https://github.com/shakenfist/kerbside/issues/300)).

An environment variable still beats the file: Kerbside copies each INI
key into `KERBSIDE_<KEY>` only when that variable is not already set.
So a systemd drop-in with `EnvironmentFile=` pointing at a root-owned
0600 file can override even a setting the role owns, and the role
writes the units but not their `.d/` directories, so a redeploy keeps
it. Use `EnvironmentFile=` rather than `Environment=`, which
`systemctl show` prints to any local user. The role neither writes
such a drop-in nor notices a change to one, so you apply it with
`systemctl daemon-reload` and a restart by hand, and the role's own
migration and readiness check — which run outside the units — do not
see it. A login configured that way also needs its client libraries
installed into the virtual environment by hand, because the role sees
no `keystone_*` setting to install them for. Prefer
`kerbside_extra_settings`.

## Status and limitations

Kerbside is [experimental](/components/kerbside/index/#kerbside-is-experimental)
overall. The sanity job runs the role's `validate`, `bootstrap`,
`config` and `register` entry points without root
(`tools/ci-test-ansible-role.sh`), with a source of each of the five
types it accepts -- `shakenfist`, `ovirt`, `openstack`, `proxmox` and
`static` -- a stub virtual environment, and no migration or units. It
also lints, builds and import-checks the collection.
[`sf-e2e`](/components/kerbside/testing/#the-shaken-fist-end-to-end-lane-sf-e2e)
deploys each pull request's Kerbside through the role, on a Shaken
Fist node, and checks that a second deploy restarts nothing.

| Not covered | Why |
|-------------|-----|
| No lane deploys an `ovirt` or `openstack` source through the role | The role installs each type's client libraries and the rootless test proves which ones it asks for, but no lane deploys such a source against a real engine or Keystone, so nothing proves end to end that a role-deployed Kerbside serves one. `ovirt-engine-sdk-python` is also built from source here, which the rootless test's stub installer does not exercise. |
| No lane deploys a `proxmox` source through the role | The role validates and renders a `proxmox` source, a unit test checks its required fields, and the rootless test renders one. No lane starts a role-deployed Kerbside with one. [`proxmox-functional.yml`](/components/kerbside/testing/#the-proxmox-end-to-end-lane) does prove a proxied SPICE session against a real single-node Proxmox VE 9, but it deploys Kerbside on the runner rather than through the role, and no lane has run either against a multi-node PVE cluster. |
| No lane deploys an extra setting | The rootless test renders `kerbside_extra_settings` into `kerbside.ini` and proves a bad name is refused, but no lane starts a Kerbside configured through one, so nothing proves end to end that a Keystone login deployed this way works. Interactive login has no lane of its own either ([#300](https://github.com/shakenfist/kerbside/issues/300)). |
| Kerbside is started only with a Shaken Fist source, co-located | `sf-e2e` is the only lane that runs `register` against a real database and systemd, and it deploys one `shakenfist` source on a Shaken Fist node. The rootless test renders every source type, but skips the migration and the units. No lane starts a role-deployed Kerbside with a `static` source, or on a host of its own. |
| Only through Shaken Fist's `site.yml` | `sf-e2e` calls the role from Shaken Fist's playbook. No lane calls it from a playbook of its own, as the second route above does. |
| One Kerbside host | No lane runs two Kerbside hosts sharing a database. Shaken Fist's [Load balancing](https://shakenfist.com/operator_guide/kerbside/#load-balancing) describes the shape, and says it is untested. |
| The API is plain HTTP | The role binds gunicorn to every address with no TLS, and no lane puts a reverse proxy in front of it. Until something binds it to loopback or firewalls it, port 13002 serves tokens and credentials in the clear. |
| A redeploy ends live sessions | A redeploy that changes Kerbside restarts it on every host in the same task, dropping every session at once, unless the play runs with `serial`. |
| Nobody can log in without Keystone | Interactive login is Keystone-only ([#300](https://github.com/shakenfist/kerbside/issues/300)), and the role configures no Keystone. Without one, people cannot log into the web UI, and something else has to hold credentials and call the API. |
