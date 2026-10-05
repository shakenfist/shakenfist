# Shaken Fist Ansible collection (`shakenfist.shakenfist`)

This collection deploys [Shaken Fist](https://shakenfist.com/) — an
opinionated, minimal cloud orchestration platform for VM and network
management — onto hosts you already manage with Ansible.

The guiding principle is that Shaken Fist deploys its `sf-*` daemons on the
hosts you tell it about, against infrastructure (MariaDB, the network node)
whose addresses you tell it. The roles express *what a node runs* as plain
role variables; **your** playbook maps your inventory groups onto those
variables. No role in this collection reads an inventory group name or
another host's facts, so the roles compose cleanly with any inventory layout.

## Roles

| Role | Purpose |
|------|---------|
| `shakenfist.shakenfist.node` | Core per-node setup: OS packages, `/etc/sf/config`, `sfrc`, the global auth file, all `sf-*` systemd units, and registration of the node and its daemons. Folds in the database capability: when `node_is_database_node` is true it also writes, registers and starts `sf-database`. Has `bootstrap`, `config` and `register` entry points (run them as separate plays to order database-tier hosts first). The `bootstrap` entry point creates the `/srv/shakenfist` virtualenv and installs the `shakenfist` server and client packages (override `server_package`/`client_package`/`pip_extra` to install local wheels for local/CI). Re-running the role is **restart-on-change**: it only restarts a node's daemons when the installed code, `/etc/sf/config`, or a systemd unit actually changed (see [Idempotence and restart-on-change](#idempotence-and-restart-on-change)). |
| `shakenfist.shakenfist.hypervisor` | Hypervisor host preparation: nested KVM detection/enable, KSM, `vhost_vsock`, SPICE TLS, and the libvirt AppArmor/config tweaks. Apply only to hosts where `node_is_hypervisor` is true. |
| `shakenfist.shakenfist.network` | Network node preparation: removes the distro `dnsmasq` unit, installs the DHCP/DNS templates, enables IPv4 forwarding, and validates the mesh interface MTU. Apply only to hosts where `node_is_network_node` is true. |
| `shakenfist.shakenfist.internal_ca` | Internal certificate authority: generates a CA on the control node, then issues a certificate for any host, SPICE TLS by default, and distributes the certificates to it (see [Certificates](#certificates)). |
| `shakenfist.shakenfist.kerbside` | Deploys a [Kerbside](https://github.com/shakenfist/kerbside) VDI console proxy: validates the configuration, installs Kerbside into its own virtualenv, renders its configuration, certificate and systemd units, runs its database migrations and waits for it to fetch Shaken Fist's console token signing key. Has `validate`, `bootstrap`, `config` and `register` entry points. Apply only to hosts in the optional `kerbside` group (see [Kerbside](#kerbside)). |

## Idempotence and restart-on-change

Re-running the `node` role is safe to do routinely — for example from a daily
`manage.yml`-style rotation that keeps every node on the tip of a branch. The
role restarts a node's `sf-*` daemons **only when something they depend on
actually changed**, so a redeploy on a day nothing moved leaves the running
cluster completely undisturbed. A daemon is restarted when any of the following
changed on its node:

* **The installed Shaken Fist code.** The `bootstrap` entry point hashes the
  *installed* `shakenfist` / `shakenfist_client` package files into a marker
  (`/srv/shakenfist/.deployed-code.sha256`) after each install and restarts if
  the hash moved. It deliberately hashes the installed files rather than the
  wheel: the wheels are rebuilt from source every deploy and embed build
  timestamps, so their bytes differ every run even when the source did not,
  whereas identical source always installs byte-identical files. This detects a
  real code change on a branch bump, on a same-version dirty rebuild (CI), and
  on a first install (marker absent), while staying quiet on a genuine no-op.
* **`/etc/sf/config`.** It is the daemons' shared systemd `EnvironmentFile`, so
  a change to it restarts every daemon on the node. The config is re-rendered
  every run and changes when a value moved or the node's role did (most
  importantly joining or leaving the database tier).
* **A systemd unit file.** A rewritten `sf-*.service`/`sf.target`, or removal of
  an obsolete unit, restarts the affected daemons. `systemctl daemon-reload`
  runs in the same play that writes the units, before any restart.

The decision is per node and is logged during `register` as a
`restart_needed=… (code=… config=… units=…)` line. Granularity is
node-wide: because the code and config are shared by every daemon on the host,
any change restarts all of that node's daemons rather than a subset. On a
database-tier node the restart still honours the ordering the role guarantees —
`sf-database` first, then a wait until its gRPC gateway (port 13005) is
accepting connections, then the remaining daemons — so a rolling redeploy of a
redundant database tier stays outage-free. Registration of the node and its
daemons is idempotent and runs every time regardless, which is what heals a
node whose database rows have drifted.

## Modules

The collection ships five native Ansible modules (under
`plugins/modules/`) for managing Shaken Fist resources from a playbook. They
import the `shakenfist_client` SDK and call the Shaken Fist REST API directly
— they do **not** shell out to `sf-client`.

| Module | Purpose |
|--------|---------|
| `shakenfist.shakenfist.sf_namespace` | Idempotently create or delete a namespace (`name`, `state`). |
| `shakenfist.shakenfist.sf_network` | Idempotently create or delete a network (`name`/`uuid`, `netblock`, `nat`, `dhcp`, `dns`, `state`). A changed specification deletes and recreates the network. |
| `shakenfist.shakenfist.sf_instance` | Idempotently create, replace or delete an instance (`name`/`uuid`, `cpu`, `ram`, `disks`/`diskspecs`, `networks`/`networkspecs`, `metadata`, `await`, `state`). A changed specification deletes and recreates the instance. |
| `shakenfist.shakenfist.sf_snapshot` | Snapshot an instance's disks (optionally updating a label) or delete a snapshot artifact (`instance_uuid`/`uuid`, `all`, `label`, `state`). |
| `shakenfist.shakenfist.sf_claim` | Idempotently create, resize, re-date or delete a namespace's capacity claim (`namespace`, `limit_cpus`, `limit_memory_mb`, `limit_disk_gb`, `expires_in_seconds`, `renew_within_seconds`, `state`). Administrator only. |

Every module accepts optional `api_url`, `namespace` and `key` connection
parameters. When all three are supplied they are used verbatim; when omitted,
the module auto-discovers credentials from the environment and
`sfrc`/`~/.shakenfist`/`/etc/sf/shakenfist.json` exactly like the `sf-client`
CLI. Supplying only some of them is an error rather than a silent fall back to
whatever credentials the control node happens to hold. `sf_claim` authenticates
as `auth_namespace` rather than `namespace`, because claim management is
administrator only and there `namespace` names the namespace the claim covers.
Which modules also accept their identity parameter on its own, because it
names the object to operate on as well, is tabulated in
[the Ansible user guide](https://shakenfist.com/user_guide/ansible/) rather
than repeated here. Each module returns `changed`, `failed` and a `meta`
object describing the resource; every module but `sf_snapshot` also returns a
`log` list of progress messages for debugging.

## Requirements

The modules require the Shaken Fist client SDK on the Ansible control node:

```bash
pip install shakenfist-client
```

(or `pip install -r requirements.txt` from the collection root). The control
node never needs the Shaken Fist server package. The roles additionally
require `ansible >= 2.15` (see `meta/runtime.yml`).

`sf_claim` needs more than that. It calls the namespace capacity claim verbs
(`get_namespace_claims`, `create_namespace_claim`, `update_namespace_claim`
and `delete_namespace_claim`), which are on the client's `develop` branch and
are **not in any release yet**, so there is no minimum released version to
ask for. A control node which uses `sf_claim` installs the client from git:

```bash
pip install git+https://github.com/shakenfist/client-python@develop
```

which is how the Shaken Fist CI conductor installs it. The module checks the
client it built for those verbs and fails with this advice, naming the verb
it could not find, rather than raising an `AttributeError`. Every other
module in the collection works with the released client.

The `internal_ca` role generates certificates on the control node with
`certtool` from the `gnutls-bin` package (Debian/Ubuntu), and checks their
expiry there with `openssl` from the `openssl` package. The role installs both
via apt when its control-node tasks run with root; rootless deploys must
install them beforehand.

## Certificates

The `internal_ca` role generates a certificate authority on the control node
and signs a host certificate with it. By default it issues the SPICE TLS
certificate for a hypervisor and installs the CA certificate, host certificate
and host key into `/etc/pki/libvirt-spice/` as `ca-cert.pem`, `server-cert.pem`
and `server-key.pem`. The role can issue a certificate for any host: the
parameters below choose the name, subject names, lifetime and install location.
Certificates are issued with an explicit lifetime of `cert_expiration_days`,
365 days by default. The SPICE private key is still installed mode `0444` by
default (issue 4416 tracks changing that).

Renewal happens during a deploy (a `site.yml` run), and only then. A
certificate is reissued when the control node's copy expires within
`cert_renew_days` (default 90) days, or when the rendered certtool template
changes (the common name, the subject alternative names or the lifetime). The
existing key is reused, and the old certificate is kept beside the new one on
the control node as `<host_cert_path>.<UTC timestamp>`, where the timestamp is
formatted `YYYYmmddHHMMSS`. The role never deletes these set-aside copies, so
one accumulates per host per reissue; prune them yourself if you need to, and
expect them to appear as new files if `ca_path` is under version control. The
template is only rewritten after the old certificate has been set aside, so a
deploy interrupted part way through a reissue repeats it next time rather than
forgetting it. A cluster therefore needs a deploy at least once inside each
renewal window: with the defaults, at least once in the last 90 days before a
certificate expires.

Until this release, renewal never worked (issue 4415), and certtool's default
lifetime of 365 days applied silently, so every SPICE certificate the
collection issued expired one year after the first deploy. The first deploy
after upgrading reissues every host's certificate, with the same key and
subject, because the template gained an explicit lifetime. Pinning by CA and
host subject is unaffected, and running instances are not disturbed.

QEMU loads SPICE TLS certificates once, when the instance starts, and never
reloads them. An instance started before a renewal therefore keeps presenting
its old certificate until its QEMU process restarts, and fails TLS verification
if it is still running when that certificate expires. A live migration starts a
new QEMU process on the destination, which loads the current files, so it
refreshes the certificate without a restart. To check the certificate a host
will hand to new instances:

```bash
openssl x509 -in /etc/pki/libvirt-spice/server-cert.pem -noout -enddate
```

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `cert_name` | `hostname` | Name used for the host's file names on the control node. |
| `cert_cn` | `hostname` | Subject common name of the certificate. |
| `cert_san_dns` | `[]` | Extra DNS subject alternative names. |
| `cert_san_ip` | `[]` | Extra IP subject alternative names. |
| `cert_expiration_days` | `365` | Lifetime in days of an issued certificate. |
| `cert_renew_days` | `90` | Reissue when fewer than this many days remain. |
| `cert_dest_dir` | `/etc/pki/libvirt-spice` | Directory on the host the files are installed into. |
| `cert_dest_ca_name` | `ca-cert.pem` | File name of the CA certificate. |
| `cert_dest_cert_name` | `server-cert.pem` | File name of the host certificate. |
| `cert_dest_key_name` | `server-key.pem` | File name of the host private key. |
| `cert_owner` | `root` | Owner of the installed files. |
| `cert_group` | `root` | Group of the installed files. |
| `cert_mode` | `'0444'` | Mode of the installed CA and host certificates. |
| `cert_key_mode` | `'0444'` | Mode of the installed host private key. |

The modes must be quoted four digit octal strings, as the defaults are: YAML
reads an unquoted `0400` as the integer 256, which can end up installed as mode
`0256`, so the role refuses it. The role also refuses a `deploy_name`,
`cert_cn` or subject alternative name containing a newline, since each is
written into a certtool template as a line of its own, and a `cert_name` or
`cert_dest_*_name` that is not a plain file name.

## Kerbside

The `node` role handles the Shaken Fist side of a
[Kerbside](https://github.com/shakenfist/kerbside) VDI console proxy, and the
`kerbside` role deploys Kerbside itself onto the hosts in the optional
`kerbside` inventory group. Nothing below does anything unless
`kerbside_url` is set, and Kerbside is only deployed when the `kerbside` group
has members.

### The Shaken Fist side

| Variable | Default | Purpose |
|----------|---------|---------|
| `kerbside_url` | `""` | Kerbside's base URL, rendered into `/etc/sf/config` and used as the token audience. When set, the deploy ensures the console token signing key exists before any daemon restarts. |
| `kerbside_token_duration` | `300` | Lifetime in seconds of a minted console token. Must be positive. |
| `kerbside_system_key` | `""` | The key Kerbside authenticates with, minted as the `kerbside` key in the system namespace on every deploy. A secret; requires `kerbside_url`, at least 16 characters, different from `system_key`. |

Changing `kerbside_system_key` rotates the key. Removing it does not delete
the key; revoke it with `sf-client namespace delete-key system kerbside`.

The deploy stops, before writing anything, if a `KERBSIDE_URL` or
`KERBSIDE_TOKEN_DURATION` `cluster_config` row differs from its variable or
`extra_config` sets either, since the row would override the variable. Run
`sf-ctl unset-config <NAME>` and deploy again. See the
[VDI console tokens operator guide](https://github.com/shakenfist/shakenfist/blob/develop/docs/operator_guide/vdi_console_tokens.md).

### Deploying Kerbside

The `kerbside` group is optional. Its members may be Shaken Fist nodes
(co-located) or hosts which are not (dedicated). Deploying needs these
variables, all of which the deploy refuses to proceed without:

| Variable | Purpose |
|----------|---------|
| `kerbside_url` | As above. Also Kerbside's console token audience. |
| `kerbside_system_key` | As above. Also Kerbside's credential for Shaken Fist. A secret. |
| `kerbside_public_fqdn` | The name SPICE clients use to reach the proxy. |
| `kerbside_sql_url` | The SQLAlchemy URL of Kerbside's MySQL or MariaDB database, for example `mysql://kerbside:PASSWORD@db.example.com/kerbside`. A secret. |
| `kerbside_auth_secret_seed` | Seeds Kerbside's token signing. A secret of at least 32 characters, and never Kerbside's placeholder `~~unconfigured~~`. |

`kerbside_url` and `kerbside_system_key` must be visible to both the `allsf`
and the `kerbside` hosts, for example in `group_vars/all`: the deploy refuses a
`kerbside_url` which the two sides see differently, since every console token
would then fail Kerbside's audience check. The other three may live in
`group_vars/kerbside`. Every Kerbside port (`kerbside_api_port`,
`kerbside_vdi_secure_port`, `kerbside_vdi_insecure_port` and
`kerbside_metrics_port`) must be below 30000 and distinct from the others, and
on a Kerbside host which is also a Shaken Fist node none may be 13000, 13001,
13005, 13006 or 13007, the ports Shaken Fist's daemons listen on. Every Kerbside
host is validated with its own variables, before any host is changed.
Kerbside fetches the signing key from `api_url`, so `api_url` must not be a
loopback address on a dedicated Kerbside host (a co-located one reaches its
own `sf-api` there).

**Database.** Bring your own MySQL or MariaDB: create a `kerbside` database and
a user with all privileges on it, and give its URL as `kerbside_sql_url`.
Kerbside does not support sqlite. The deploy runs Kerbside's migrations.

**Certificate.** Unless you override it, the proxy certificate is issued from
the deployment's internal CA into `/etc/kerbside/pki`, with `kerbside_public_fqdn`
as its CN and DNS SAN. To use your own, set all three of
`kerbside_proxy_cert_path`, `kerbside_proxy_key_path` and `kerbside_cacert_path`
to paths on the Kerbside host; all or none.

**Package.** `kerbside_package` defaults to `kerbside>=0.7.0`, because Kerbside
0.6.0's API returns source passwords, which here is the cluster's `kerbside`
key. Until 0.7.0 is released, set `kerbside_package` to a version or a wheel. A
local wheel must be built from a git checkout of Kerbside, not an unpacked
sdist ([kerbside#326](https://github.com/shakenfist/kerbside/issues/326)).

**Redeploys.** Each deploy waits, for up to about three minutes
(`kerbside_ready_retries`), for Kerbside to fetch the signing key with the
credential that deploy minted. A redeploy restarts Kerbside only when its
code (including the version of any package in its virtualenv other than pip
and uv), configuration, systemd units or certificate changed.

Without a `kerbside` group, ansible prints `Could not match supplied host
pattern, ignoring: kerbside`. This is harmless; defining an empty `kerbside:`
group in your inventory silences it.

Kerbside's admin login is Keystone-only, and the role configures no
Keystone, so its admin UI is unavailable
([kerbside#300](https://github.com/shakenfist/kerbside/issues/300)); consoles
work. The role is new, and its cluster test arrives with the merge-queue lane,
so it has not yet run in CI against a real cluster.

## Consuming the collection

Install the published collection on your Ansible control node:

```bash
ansible-galaxy collection install shakenfist.shakenfist
```

Then reference the roles by their fully-qualified collection name (FQCN) from
your own playbook, gating the capability roles on the relevant variables:

```yaml
- hosts: all
  become: true
  roles:
    - role: shakenfist.shakenfist.node

- hosts: hypervisors
  become: true
  roles:
    - role: shakenfist.shakenfist.hypervisor

- hosts: network_node
  become: true
  roles:
    - role: shakenfist.shakenfist.network

- hosts: all
  become: true
  roles:
    - role: shakenfist.shakenfist.internal_ca
```

Your playbook is responsible for computing each role's input variables (for
example the per-node capability flags and any cluster-wide values) from your
inventory and passing them in. Example playbooks that demonstrate this mapping
ship under `examples/` in the Shaken Fist repository.

See each role's `meta/argument_specs.yml` for its full, documented variable
set.

## License

Apache-2.0. Copyright 2019 Michael Still and contributors.
