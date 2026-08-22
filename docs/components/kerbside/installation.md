# Installation

This page takes you from nothing to a working, proxied SPICE console,
and then points at the guide for the cloud you actually run. If you
would rather see Kerbside working before reading about it, skip to
[Try it: the demo stack](#try-it-the-demo-stack).

## Before you install: OS packages

`pip install kerbside` builds `mysqlclient` from source — that package
ships no wheel — so a clean machine needs a compiler, the MariaDB
client headers and `pkg-config` before pip will get anywhere. Without
them the install fails at `Can not find valid pkg-config name`, or
later in the compile, which reads as a Python problem and is not one.

On Debian:

```bash
sudo apt-get install build-essential pkg-config python3-dev \
    libmariadb-dev-compat libxml2-dev libxslt1-dev
```

On Ubuntu the same list applies with `libmysqlclient-dev` in place of
`libmariadb-dev-compat`. On RHEL, CentOS and Fedora:

```bash
sudo dnf install gcc pkgconfig python3-devel mariadb-devel \
    libxml2-devel libxslt-devel
```

You will also want a MariaDB or MySQL server, either locally or
reachable over the network — see
[What a running Kerbside needs](#what-a-running-kerbside-needs).

From a checkout, `tox -e bindep` reports the same thing against
`bindep.txt`, per platform. It needs the source tree and `tox`, so it
is a developer convenience rather than a step for someone installing
from PyPI.

## Installing with pip

```bash
pip install kerbside
```

Kerbside is split into two packages: the pure-Python `kerbside` (the
REST API, the console-source drivers, the SQLAlchemy data model, and
the daemon that supervises the proxy) and the Rust `kerbside-proxy`
(the SPICE proxy itself, `rust/kerbside-proxy/` in the source tree).

`kerbside-proxy` is published to PyPI as a separate maturin
`bindings = "bin"` wheel that carries the compiled binary and lands it
on `PATH` (the daemon finds it via `shutil.which('kerbside-proxy')`);
you do not build or install it separately. A release install —
`pip install kerbside==X.Y.Z` — gets the exact-pinned `kerbside-proxy`
release it was built and tested against: the two packages release in
lockstep from a single `v*` tag. A git or dev install of `kerbside`
instead resolves whatever `kerbside-proxy` is newest on PyPI, which may
be a rolling dev wheel rather than a tagged release. Prebuilt manylinux
wheels are published for x86_64 and aarch64 (no source distribution —
an unsupported platform gets a clean pip error).

For development you can instead point `KERBSIDE_PROXY_BIN` at a
locally built binary, or let the daemon pick up the in-repo
`cargo build` output.

At startup, the daemon runs `kerbside-proxy --contract-hash` and
refuses to launch a binary whose gRPC contract does not match this
`kerbside` version's — the error names both hashes and lists the ways
to fix it. `KERBSIDE_SKIP_CONTRACT_CHECK=1` is the explicit, unsupported
escape hatch that downgrades the refusal to a logged warning. See
[proxy-architecture.md](/components/kerbside/proxy-architecture/) for how the check works.

## What a running Kerbside needs

Installing the package gives you commands, not a running system. This
section is the mental model: what has to exist before a console works.
[configuration.md](/components/kerbside/configuration/) is the reference for every
setting; this is the short list of things you cannot skip.

**Two processes, co-located.** The REST API is a WSGI application
served by gunicorn, and the SPICE proxy is a daemon that supervises
the Rust binary:

```bash
gunicorn --bind 0.0.0.0:13002 --workers 2 'kerbside.api:app'
kerbside daemon run
```

Neither is optional and they are not interchangeable: the API is where
brokers and users ask for consoles, and `daemon run` is what discovers
consoles and moves SPICE traffic. They talk to each other over a unix
socket (`API_SOCKET_PATH`, default `/run/kerbside/api.sock`), so **they
must be co-located** — the same host, the same container, or two
containers sharing that path. Note that the API's port is a gunicorn
argument rather than a Kerbside setting; `PUBLIC_FQDN` is how Kerbside
tells clients where to find it.

That gunicorn line is the demo's shape, not a recommendation: it
serves plain HTTP on every interface, and the API carries bearer
tokens and Keystone credentials. A real deployment binds it to an
interface you have chosen and puts TLS in front of it — a reverse
proxy, or gunicorn's own `--certfile` and `--keyfile`. The proxy leg's
TLS settings below do not cover the API leg; they are separate.

**A database.** MySQL or MariaDB, reached via `SQL_URL`. Create the
database and its user yourself, then let Kerbside create its own
schema:

```bash
kerbside db upgrade
```

Run that before starting either process, and again after every
upgrade. It is idempotent, so a service unit or container entrypoint
can call it unconditionally. It reads `SQL_URL` from the configuration
described below, so write that first: with no configuration at all,
`SQL_URL` still has a Kolla-shaped placeholder default and the command
fails with `Unknown server host 'kolla'`, which is a missing config
file rather than a database problem.

**TLS material for the proxy leg.** Clients reach the proxy over TLS
and verify it against a CA you provide. Four settings carry this:
`CACERT_PATH`, `PROXY_HOST_CERT_PATH`, `PROXY_HOST_CERT_KEY_PATH` and
`PROXY_HOST_SUBJECT`. The last is the trap — it is the subject string
clients are told to expect, so it must match the subject of the
certificate the proxy actually presents. When those disagree, the
viewer refuses the connection and the error does not explain itself.

**A console source.** Kerbside proxies consoles it has discovered, so
it needs at least one source in the file named by `SOURCES_PATH`,
conventionally `/etc/kerbside/sources.yaml`. The drivers are Shaken
Fist, oVirt, OpenStack, and `static` for fixed targets — see
[console-sources.md](/components/kerbside/console-sources/).

**The minimum configuration set.** Configuration is an INI file at
`/etc/kerbside/kerbside.ini`; that path is hardcoded, and environment
variables prefixed with `KERBSIDE_` override it. These eight settings
are the ones with no useful default, and they are exactly the ones
left live in
[etc/kerbside.conf.example](https://github.com/shakenfist/kerbside/blob/develop/etc/kerbside.conf.example),
which documents every setting Kerbside has:

| Setting | What it is |
|---|---|
| `sql_url` | The database, as a SQLAlchemy URL |
| `auth_secret_seed` | The seed session tokens are signed with; generate a fresh one per deployment |
| `sources_path` | Where `sources.yaml` lives |
| `public_fqdn` | The name clients should reach this Kerbside by |
| `cacert_path` | The CA clients verify the proxy against |
| `proxy_host_cert_path` | The proxy's certificate |
| `proxy_host_cert_key_path` | Its key |
| `proxy_host_subject` | The subject clients are told to expect |

## Try it: the demo stack

`demo/` in this repository brings all of that up in three containers,
so you can watch a proxied console work before deciding how to deploy
one. It needs nothing but Docker Engine 23.0 or newer with the Compose
v2 plugin — no Python, no database, and no certificates of your own.
On Debian 12, install Docker from
[Docker's own repository](https://docs.docker.com/engine/install/debian/)
rather than from the distribution, which ships an engine too old to
build the image.

`pip install` does not give you `demo/`, so start from a checkout:

```bash
git clone https://github.com/shakenfist/kerbside
cd kerbside/demo
docker compose up -d
```

One thing to know about that pairing: the clone lands on `develop`,
but the image installs the most recent *release* from PyPI, so the
demo's glue comes from the checkout while Kerbside itself does not.
That is deliberate — what you evaluate is what you can install — and
CI does not exercise it, because the lane builds from the checkout
instead. If you have changed anything under `demo/`, or want a
stack that matches the tree you are sitting in, build it that way:
`KERBSIDE_SOURCE=/src docker compose build kerbside`, which needs an
ordinary clone rather than a git worktree.
[demo/README.md](https://github.com/shakenfist/kerbside/blob/develop/demo/README.md)
covers both forms.

The first run builds the image and takes a few minutes; afterwards the
stack comes up in seconds. When it returns, three containers are
running — MariaDB, a disk-less qemu with a SPICE server, and Kerbside
with both of its processes:

```
$ docker compose ps --format 'table {{.Service}}\t{{.Status}}'
SERVICE        STATUS
db             Up 7 minutes (healthy)
kerbside       Up 7 minutes (healthy)
spice-target   Up 7 minutes
```

Now mint a token and fetch a console file:

```bash
./get-console.sh
```

The script mints a bearer token, finds the demo console, writes
`demo-console.vv`, and then proves the TLS leg rather than assuming
it: it connects to the port the `.vv` advertises and verifies the
presented certificate against the CA embedded in that same file.

```
[demo] Waiting for the stack to finish starting...
[demo] Minting a bearer token...
WARNING: this is a demonstration token, minted directly from
AUTH_SECRET_SEED because kerbside has no non-Keystone login (issue
#300). Do not use this pattern in production.
Token written to /tmp/demo-token.169928
[demo] Waiting for the console list...
[demo] Looking up the console...
[demo] Console: demo-console (demo/0d3f6a52-0000-0000-0000-0000000dec01)
[demo] Fetching the .vv file...
[demo] Verifying the TLS leg...
    TLSv1.3, certificate verified against the CA in the .vv
    subject matches host-subject=C=US,O=Kerbside CI,CN=kerbside-ci
[demo] Wrote ./demo-console.vv
```

(The script then repeats the `remote-viewer` command and the
expectation below, which is the next step here.)

Then open it. `remote-viewer` ships in the `virt-viewer` package
(`sudo apt-get install virt-viewer`, or `sudo dnf install
virt-viewer`), and it is the only thing you need on the client side:

```bash
remote-viewer ./demo-console.vv
```

**Expect a black screen with boot firmware text, and expect the boot
to fail.** The demo VM has no disk, so it tries the network, then a
floppy, then gives up:

```
iPXE (PCI 00:02.0) starting execution...ok
net0: 52:54:00:12:34:56 using 82574l on 0000:00:02.0 (Ethernet) [open]
Nothing to boot: No such file or directory
Booting from Floppy...
Boot failed: could not read the boot disk

No bootable device.
```

That screen *is* the SPICE session: it is being drawn by a real qemu
and relayed through Kerbside over TLS. The viewer's title bar names
the proxy session it is attached to. If you want to confirm the
traffic took the TLS port rather than the plaintext one, count
established sockets while the viewer is open — they should all be on
5900, and none on 5901:

```bash
ss -H -tn state established '( dport = :5900 or sport = :5900 )' | wc -l
ss -H -tn state established '( dport = :5901 or sport = :5901 )' | wc -l
```

On a live session that prints `12` and `0`. The `-H` matters: without
it `ss` emits a header line and both counts come back one too high,
which makes the plaintext port look used when it is not.

When you are finished:

```bash
docker compose down -v
```

That removes the containers, the database, and the generated CA and
signing seed — both of which are created on first start rather than
baked into the image.

### Where the token comes from

`get-console.sh` runs `kerbside demo token` inside the container, and
that command prints a warning every time it succeeds. The warning is
worth reading: the command exists because interactive login is
Keystone-only today
([#300](https://github.com/shakenfist/kerbside/issues/300)), so a
static deployment gives a human no way to log in at all. It stands in
for authentication in a demonstration; it is not a preview of how your
users will get consoles.

It also refuses to mint unless every configured source is `static`,
and refuses while the signing seed is unconfigured. Those refusals are
deliberate rather than configuration problems to work around — in a
real deployment a broker mints session tokens through the API. If
`kerbside demo token` refuses on the demo stack, something else is
genuinely wrong.

### What the demo is not

| Limitation | Detail |
|---|---|
| No real authentication | Login is Keystone-only ([#300](https://github.com/shakenfist/kerbside/issues/300)), and the session JWT scheme has no revocation or issuance audit ([#301](https://github.com/shakenfist/kerbside/issues/301)). The web UI on `http://127.0.0.1:13002` cannot be logged into |
| A self-signed CA | Generated into a volume on first start, and destroyed by `down -v` |
| Loopback only | Every published port binds to `127.0.0.1` on purpose. Widening it puts an unauthenticated console proxy on your network |
| Two processes in one container | The shortest way to co-locate them for a demo, not a shape to copy. Real deployments run them as separate units sharing the socket |
| A plaintext backend leg | Kerbside talks plaintext SPICE to the qemu container. Real deployments should pin the backend too; [ovirt.md](/components/kerbside/use-cases/ovirt/) shows what that looks like |
| A static source, not a cloud | The `static` driver points at a fixed target, so nothing is being discovered from a real cloud |

[demo/README.md](https://github.com/shakenfist/kerbside/blob/develop/demo/README.md)
is the reference for the stack itself: what each container is, how to
build it against a checkout rather than the released package, and why
each of these choices was made.

## Deploying for real

Kerbside is deployed as a component of the cloud it serves, so the
shape of a real deployment depends on which cloud that is. Start with
the guide for yours:

| Deployment | Guide |
|---|---|
| oVirt | [use-cases/ovirt.md](/components/kerbside/use-cases/ovirt/) |
| OpenStack | A sample Kolla-Ansible deployment lives in [kerbside-patches](https://github.com/shakenfist/kerbside-patches) |
| Shaken Fist | [console-sources.md](/components/kerbside/console-sources/#shaken-fist), until the use-case page is written |
| Static targets | The demo above is the worked example |

Whichever you pick, the pieces described in
[What a running Kerbside needs](#what-a-running-kerbside-needs) are
the same, and two references apply throughout:
[configuration.md](/components/kerbside/configuration/) for every setting, and
[console-sources.md](/components/kerbside/console-sources/) for configuring sources.
