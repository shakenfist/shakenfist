# Deploying Kerbside by hand

This page installs Kerbside on one host with pip, a virtual
environment and two systemd units, and leaves it running. It is the
same layout the `kerbside` Ansible role produces, written out as
commands, with one deliberate difference in where the API listens.

## Who this is for

An operator who will not run Ansible, or who runs another
configuration manager and wants to know what to make it do. Every
step below says what to create and why, so it translates into a Salt
state, a Puppet manifest or a shell script as readily as into typing.

If you can run Ansible, the role in Kerbside's `shakenfist.kerbside`
collection ([Deploying Kerbside with Ansible](/components/kerbside/deployment/ansible/)) is the
tested way to get the same result: a CI lane deploys Kerbside
through it on every pull request. This page follows the role's tasks,
and says so wherever it does something different.

This page is about how Kerbside is installed and run, not about why.
For what Kerbside is worth in front of a particular cloud, start with
the [use cases](/components/kerbside/index/#use-cases). The pieces any running
Kerbside needs (its two processes, a database, TLS material for the
proxy, a console source and the settings with no useful default) are
described once, in
[What a running Kerbside needs](/components/kerbside/installation/#what-a-running-kerbside-needs),
and this page assumes you have read it.

## What it deploys

Two systemd units, one for each of Kerbside's processes:

- [etc/systemd/kerbside-daemon.service](https://github.com/shakenfist/kerbside/blob/develop/etc/systemd/kerbside-daemon.service)
  runs the daemon, which discovers consoles and supervises the SPICE
  proxy as its child. Stopping it stops the proxy.
- [etc/systemd/kerbside-api.service](https://github.com/shakenfist/kerbside/blob/develop/etc/systemd/kerbside-api.service)
  runs the REST API under gunicorn, on `127.0.0.1:13002` with two
  workers.

Both run as the `kerbside` user, restart five seconds after any exit
that systemd did not ask for, and are wanted by `multi-user.target`.
The comments in each file give the reasons behind its directives.

Every file this page has you copy, the units included, lives in the
repository rather than in the wheel. The links here go to `develop`,
which is the code being worked on rather than the release you
install. Take the files from that release's tag instead, at
`https://github.com/shakenfist/kerbside/tree/vX.Y.Z` or in a checkout
of it, so that they match the code they configure. Releases up to and
including v0.8.0 predate the example units, so this page needs a later
one.

On disk, the result is:

| Path | Owner | Mode | What it is |
|------|-------|------|------------|
| `/srv/kerbside` | `root:root` | 0755 | The `kerbside` user's home, holding only the virtual environment |
| `/srv/kerbside/venv` | `root:root` | as created | Kerbside, `kerbside-proxy`, gunicorn and any client libraries your sources need |
| `/etc/kerbside` | `root:kerbside` | 0750 | Configuration |
| `/etc/kerbside/kerbside.ini` | `root:kerbside` | 0640 | Kerbside's settings, including the token signing seed and the database password |
| `/etc/kerbside/sources.yaml` | `root:kerbside` | 0640 | Console sources, including each source's credentials |
| `/etc/kerbside/pki` | `root:root` | 0755 | The proxy's certificate and CA |
| `/etc/kerbside/pki/server-key.pem` | `root:kerbside` | 0640 | The proxy's private key |
| `/etc/systemd/system/kerbside-*.service` | `root:root` | 0644 | The two units |
| `/run/kerbside` | `kerbside` | 0700 | The daemon's API socket, mode 0600. systemd creates the directory (`RuntimeDirectory=kerbside`) each time the daemon starts, and the daemon sets both modes when it binds |

The `kerbside` user is a system user with no login shell. Its home is
`/srv/kerbside`, but root owns that directory, and root owns
everything under `/etc/kerbside` as well. Kerbside reads its
configuration and never writes it, so it is given read access through
its group and nothing more. A compromised Kerbside process can then
read its secrets but cannot rewrite its own configuration, and cannot
replace the code it runs.

There is no state directory and no log directory, because Kerbside
writes almost nothing locally. Its state is in the database. Apart
from the temporary files Proxmox sources, and oVirt sources without a
`ca_file`, write their CA to, the only file it creates at runtime is
its API socket. That goes in `/run/kerbside`, a directory only root
may create, so systemd creates it for the service user. Kerbside logs
to syslog, which on a systemd host lands in the journal, so
`journalctl` is where its logs are. `LOG_OUTPUT_PATH` changes that
(see
[configuration.md](/components/kerbside/configuration/#logging-and-monitoring-settings)),
and a file it names has to be writable by the `kerbside` user.

The role also keeps its helper scripts in `/srv/kerbside/bin` and a
record of what it last deployed in `/srv/kerbside`. Neither is needed
by hand.

What it leaves to you:

- **The database server.** MySQL or MariaDB, on this host or another.
  Step 6 creates Kerbside's database on it, but not the server.
- **Certificates.** The proxy's certificate, key and CA come from
  whatever issues certificates for you.
- **TLS for the API.** The example API unit listens on loopback in
  plain HTTP, and something has to put TLS in front of it before
  anything off this host can use it. Step 9 gives the two ways.
- **The firewall.** [network-ports.md](/components/kerbside/network-ports/) lists
  every port Kerbside listens on and dials, and who needs to reach
  each.

## How to use it

Every command runs as root.

1. **Install the OS packages.** Install the build dependencies in
   [Before you install: OS packages](/components/kerbside/installation/#before-you-install-os-packages),
   including the extra two if you will use an `ovirt` source. The
   role installs the same Debian list.

2. **Create the user and group.**

   ```bash
   groupadd --system kerbside
   useradd --system --gid kerbside --no-create-home \
       --home-dir /srv/kerbside --shell /usr/sbin/nologin kerbside
   ```

   The home is named but not created here, because the next step
   creates it owned by root rather than by `kerbside`.

3. **Create the virtual environment and install Kerbside.**

   ```bash
   install -d -o root -g root -m 0755 /srv/kerbside
   python3 -m venv /srv/kerbside/venv
   /srv/kerbside/venv/bin/pip install -U pip
   /srv/kerbside/venv/bin/pip install kerbside==X.Y.Z
   ```

   Install a tagged release, for the reason
   [Installing with pip](/components/kerbside/installation/#installing-with-pip)
   gives: a release pins the exact `kerbside-proxy` it was tested
   with. gunicorn is one of Kerbside's dependencies, so it comes with
   it. The virtual environment does not see the system's
   site-packages, because Kerbside needs nothing from them.

   Then install the client libraries your sources and your logins
   need into the same environment, from
   [Client libraries for console sources](/components/kerbside/installation/#client-libraries-for-console-sources).
   Kerbside starts without them, and the gap shows up later, as an
   ignored source or an API error. The role installs
   `shakenfist-client` when a source is of type `shakenfist`, and
   nothing else, so for any other source this step does more than the
   role does.

   The role installs with `uv pip install --reinstall` rather than
   pip, so that a rebuilt wheel with an unchanged version is picked
   up. A tagged release from PyPI does not need that, and pip does
   the same job.

4. **Write the configuration.** Create the directory and both files
   with their final owner and mode before putting anything secret in
   them:

   ```bash
   install -d -o root -g kerbside -m 0750 /etc/kerbside
   install -o root -g kerbside -m 0640 kerbside.conf.example \
       /etc/kerbside/kerbside.ini
   install -o root -g kerbside -m 0640 /dev/null \
       /etc/kerbside/sources.yaml
   ```

   `kerbside.conf.example` is
   [etc/kerbside.conf.example](https://github.com/shakenfist/kerbside/blob/develop/etc/kerbside.conf.example),
   which documents every setting and leaves exactly the ones with no
   useful default uncommented. Fill those in; the table in
   [What a running Kerbside needs](/components/kerbside/installation/#what-a-running-kerbside-needs)
   says what each is. Keep `sources_path` at
   `/etc/kerbside/sources.yaml`, as the example has it. Its built-in
   default is the relative `./sources.yaml`, which under these units
   would mean `/srv/kerbside/sources.yaml`.

   Write your sources into `sources.yaml`.
   [console-sources.md](/components/kerbside/console-sources/) describes each type,
   and
   [etc/example-static-sources.yaml](https://github.com/shakenfist/kerbside/blob/develop/etc/example-static-sources.yaml)
   is a starting point for a `static` one.

   Both units run as `kerbside`, and both processes read both files,
   which is why they are group readable rather than 0600. Neither
   process can write them.

   A `kerbside.ini` that Kerbside cannot parse makes every Kerbside
   process exit at start, and under `Restart=always` that is a
   restart every five seconds. The role checks both files before it
   writes them, with
   [kerbside-check-ini.py](https://github.com/shakenfist/kerbside/blob/develop/ansible/roles/kerbside/files/kerbside-check-ini.py)
   and
   [kerbside-check-sources.py](https://github.com/shakenfist/kerbside/blob/develop/ansible/roles/kerbside/files/kerbside-check-sources.py).
   They need nothing beyond the virtual environment's Python, so you
   can run them on a file before you put it in place too.

5. **Install the proxy's TLS material.** The role reads it from
   `/etc/kerbside/pki`, and this page uses the same names:

   ```bash
   install -d -o root -g root -m 0755 /etc/kerbside/pki
   install -o root -g root -m 0644 server-cert.pem ca-cert.pem \
       /etc/kerbside/pki/
   install -o root -g kerbside -m 0640 server-key.pem \
       /etc/kerbside/pki/server-key.pem
   ```

   The proxy runs as `kerbside`, so the key is readable by its group
   and by nobody else. The certificate and CA are not secret. The
   role does not install these three files itself. It expects whatever
   issues them to have put them there, and it sets only the
   directory's mode and expects the key's, so the 0644 here is this
   page's choice. Point `cacert_path`, `proxy_host_cert_path` and
   `proxy_host_cert_key_path` in `kerbside.ini` at these three files.
   The example's values are under `/etc/pki/CA`, so all three change.

   `proxy_host_subject` must match the certificate's subject exactly,
   as [What a running Kerbside needs](/components/kerbside/installation/#what-a-running-kerbside-needs)
   explains. The role computes it from the certificate with
   [kerbside-host-subject.py](https://github.com/shakenfist/kerbside/blob/develop/ansible/roles/kerbside/files/kerbside-host-subject.py),
   which needs only the virtual environment's Python, and you can do
   the same:

   ```bash
   /srv/kerbside/venv/bin/python kerbside-host-subject.py \
       /etc/kerbside/pki/server-cert.pem
   ```

   It prints the value to use. Its comments say why `openssl x509
   -subject` is not a substitute.

6. **Create the database, then its schema.** Kerbside does not create
   its database or its user. On MariaDB:

   ```sql
   CREATE DATABASE IF NOT EXISTS kerbside;
   CREATE USER IF NOT EXISTS 'kerbside'@'localhost' IDENTIFIED BY 'CHANGEME';
   GRANT ALL PRIVILEGES ON kerbside.* TO 'kerbside'@'localhost';
   ```

   Use the host Kerbside connects from in place of `localhost` if the
   database is elsewhere, and put the same user and password in
   `sql_url`. Then create the schema, as the `kerbside` user:

   ```bash
   runuser -u kerbside -- /srv/kerbside/venv/bin/kerbside db upgrade
   ```

   This is the role's own invocation. Running it as `kerbside` rather
   than as root proves that the service user can read
   `kerbside.ini`. Look for `INI file processing complete` in what it
   prints: the role checks for that line, because without it the
   command did not read your `kerbside.ini`, and so did not migrate
   your database.

7. **Install and start the units.** Copy both unit files into
   `/etc/systemd/system/` unchanged, then:

   ```bash
   install -o root -g root -m 0644 kerbside-api.service \
       kerbside-daemon.service /etc/systemd/system/
   systemctl daemon-reload
   systemctl enable --now kerbside-daemon kerbside-api
   ```

   They expect the user, the virtual environment and the paths above,
   and need no editing if you have followed this page. The daemon
   unit names the proxy binary in `KERBSIDE_PROXY_BIN`, and Kerbside
   treats that as authoritative: if it is not executable the daemon
   fails rather than looking elsewhere.

8. **Check it works.**

   ```bash
   systemctl status kerbside-daemon kerbside-api
   journalctl -u kerbside-daemon -u kerbside-api
   curl -H 'Accept: application/json' http://127.0.0.1:13002/
   ```

   Both units should be `active (running)`. One that is
   `activating (auto-restart)` is exiting and being restarted, and
   its journal says why. The `curl` is the request the role waits
   for: `GET /` with a JSON `Accept` header needs no authentication,
   and a working API answers it with

   ```json
   {
       "capabilities": [],
       "result": "ok"
   }
   ```

   The daemon reads its sources as soon as it starts, and again every
   sixty seconds. A source that cannot be used is marked errored, and
   the daemon's journal names the reason. For a `shakenfist` source
   the role also waits for Kerbside to fetch the cluster's console
   token signing keys before it calls the deploy finished. A fetch
   that fails is logged as a warning in the same journal.

9. **Put TLS in front of the API.** The API carries bearer tokens and
   Keystone credentials, which is why the example unit listens only on
   loopback. There are two ways to serve it to other hosts:

   - **A TLS-terminating reverse proxy** on this host, forwarding to
     `127.0.0.1:13002`. Kerbside builds no URL from the request, so
     the proxy needs no forwarded-header configuration. Give it a
     read timeout at least as long as the unit's 300 seconds: the
     API can call Shaken Fist inside a request to refresh its signing
     keys, which is why gunicorn's timeout is that long.
   - **gunicorn's own TLS.** Replace the API unit's `ExecStart` in a
     drop-in rather than in the copied file, so that an upgrade can
     replace the file without losing the change. `systemctl edit
     kerbside-api` opens one. Bind to an address other hosts can
     reach, and add `--certfile` and `--keyfile` naming the API's
     certificate and key:

     ```ini
     [Service]
     ExecStart=
     ExecStart=/srv/kerbside/venv/bin/gunicorn --bind ADDRESS:13002 \
         --workers 2 --timeout 300 --name kerbside-api \
         --certfile CERTIFICATE --keyfile KEY kerbside.api:app
     ```

     The empty `ExecStart=` clears the unit's own, which systemd
     needs before it accepts another. gunicorn runs as `kerbside`, so
     that key needs the same `root:kerbside` 0640 as the proxy's.

   Neither is tested in CI. Kerbside does not ship a reverse proxy
   configuration.

   Here the page differs from the role on purpose. The role binds
   the API to `0.0.0.0` in plain HTTP. These units are files people
   copy as they are, so they are written to be safe when copied as
   they are, and that means loopback until you choose otherwise.

### Upgrading

An upgrade by hand does what the role does, in the same order:
install the new release, migrate the database, bring the units up to
date, restart.

```bash
/srv/kerbside/venv/bin/pip install kerbside==X.Y.Z
runuser -u kerbside -- /srv/kerbside/venv/bin/kerbside db upgrade
diff /etc/systemd/system/kerbside-daemon.service kerbside-daemon.service
diff /etc/systemd/system/kerbside-api.service kerbside-api.service
```

The role renders both units again on every deploy. By hand, that is
the two `diff`s, against the unit files from the new release's tag.
If either differs, install it as step 7 does and run `systemctl
daemon-reload`. A drop-in from step 9 survives that, because it is a
separate file. Then restart:

```bash
systemctl restart kerbside-daemon kerbside-api
```

Upgrading `kerbside` does not upgrade the client libraries from step
3. If the new release's
[Client libraries for console sources](/components/kerbside/installation/#client-libraries-for-console-sources)
table raises a version floor, install that library again with `pip
install -U`.

Read [Upgrading](/components/kerbside/installation/#upgrading) first. It records the
releases which need something from you as well.

Restart both units. Each loaded the old code when it started, and
the install has replaced the files underneath both of them.

**Restarting the daemon ends every live SPICE session on this host.**
The proxy is the daemon's child, so it stops with it. On `SIGTERM`
the daemon tells the proxy to stop, and the proxy ends every session
and waits up to ten seconds for them to finish tearing down. The
daemon kills it if it is still running after fifteen.
`KillMode=mixed` sends that `SIGTERM` to the daemon alone, so the
proxy hears about the stop from the daemon rather than from systemd,
and systemd kills anything left after `TimeoutStopSec=30`. That is a
clean shutdown, not a handover: users have to reconnect after it.
Upgrade when an interruption is acceptable.

### More than one host

This page deploys one Kerbside, with both processes on one host. The
daemon and its proxy always run together, but the API may run on
another host, and several Kerbsides may share a database.
[Placement topologies](/components/kerbside/use-cases/placement/) describes those
shapes, and no CI lane runs any of them. If you split the API from
the daemon, the API's host still needs the same `sources.yaml` and CA
certificate as the daemon's, because the API reads both from disk.

## Status and limitations

Kerbside is [experimental](/components/kerbside/index/#kerbside-is-experimental) overall. The procedure on this page is not
run by any CI lane, but the units it installs are close to ones that
are.

| Not covered | Why |
|-------------|-----|
| No CI lane runs these units | The units are held to the role's templates by `kerbside/tests/unit/test_systemd_units.py`, which renders the templates with the role's defaults and fails on any difference in their directives but the API's bind address. The role's units are the ones [`sf-e2e`](/components/kerbside/testing/#the-shaken-fist-end-to-end-lane-sf-e2e) deploys, on a Shaken Fist node, on every pull request and nightly. So the units are tested by being held to tested ones, not by being run. |
| No lane follows these steps | The other lanes install Kerbside with their own scripts, which do not create the user, the directories or the units this page does. A mistake in a step here would not fail CI. |
| TLS on the API is untested | Neither a reverse proxy nor gunicorn's `--certfile` and `--keyfile` is exercised anywhere, and Kerbside ships no reverse proxy configuration, because it would be a second untested file. |
| No distribution packages | There is no deb or rpm, so nothing installs Kerbside through the host's package manager or upgrades it along with the rest of the host. |
| An upgrade ends live sessions | The proxy runs as the daemon's child, so restarting the daemon restarts the proxy and drops every session on the host. There is no handover. |
| One host only | Splitting the API from the daemon, or running several Kerbsides, is described in [Placement topologies](/components/kerbside/use-cases/placement/) but not here, and no lane runs either. |
| Nobody can log in without Keystone | Interactive login is Keystone-only ([#300](https://github.com/shakenfist/kerbside/issues/300)). Without a Keystone to point at, people cannot log into the web UI, and something else has to hold credentials and call the API. |
