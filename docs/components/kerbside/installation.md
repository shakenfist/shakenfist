# Installation

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
on `PATH`. `kerbside` exact-pins `kerbside-proxy` at the same version,
so `pip install kerbside` installs a matching proxy automatically (the
daemon finds it via `shutil.which('kerbside-proxy')`); you do not
build or install it separately. Both packages are released in lockstep
from a single `v*` tag, and prebuilt manylinux wheels are published
for x86_64 and aarch64 (no source distribution — an unsupported
platform gets a clean pip error).

For development you can instead point `KERBSIDE_PROXY_BIN` at a
locally built binary, or let the daemon pick up the in-repo
`cargo build` output.

## Checking OS package dependencies

Kerbside requires certain OS-level packages to be installed. You can
check for missing dependencies using bindep via tox:

```bash
tox -e bindep
```

This will read the `bindep.txt` file and report any missing system
packages that need to be installed for your platform. The bindep tool
automatically detects your operating system and checks for
platform-specific packages.

After running the bindep check, install any missing packages using
your system's package manager:

**Debian/Ubuntu:**
```bash
sudo apt-get install <package-names>
```

**RHEL/CentOS/Fedora:**
```bash
sudo dnf install <package-names>
```

The `bindep.txt` file includes dependencies for MariaDB/MySQL client
libraries, XML parsing libraries, and build tools needed for compiling
Python extensions.

## Deployment

Kerbside is deployed as a component of the cloud it serves. For
OpenStack there is a sample Kolla-Ansible deployment implementation in
the [kerbside-patches](https://github.com/shakenfist/kerbside-patches)
repository. See [console-sources.md](/components/kerbside/console-sources/) for
configuring console sources and [configuration.md](/components/kerbside/configuration/)
for the full configuration reference.
