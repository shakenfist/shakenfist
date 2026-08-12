# Installation

Pre-built x86_64 Linux artifacts are published on every release at
[GitHub Releases](https://github.com/shakenfist/instar/releases). Pick
the format that matches your distro.

The published packages require **glibc 2.31 or newer**. The release
binary is built on Debian 11 (bullseye), so a single artifact runs
across Debian 11+, Ubuntu 22.04 LTS and 24.04 LTS, Fedora, and
Rocky/RHEL 9 and 10. (glibc is forward-compatible: a binary runs on
any host whose glibc is at least the one it was built against.)

## Debian / Ubuntu (.deb)

```bash
VERSION=0.3.0
curl -sLO "https://github.com/shakenfist/instar/releases/download/v${VERSION}/instar_${VERSION}-1_amd64.deb"
sudo apt install "./instar_${VERSION}-1_amd64.deb"
instar --help
```

## Fedora / RHEL / SUSE (.rpm)

```bash
VERSION=0.3.0
curl -sLO "https://github.com/shakenfist/instar/releases/download/v${VERSION}/instar-${VERSION}-1.x86_64.rpm"
sudo dnf install "./instar-${VERSION}-1.x86_64.rpm"
instar --help
```

The packages install the VMM at `/usr/bin/instar` and the six guest
binaries (loaded into the KVM sandbox at runtime) at
`/usr/lib/instar/`.

## Tarball (any Linux)

```bash
VERSION=0.3.0
curl -sL "https://github.com/shakenfist/instar/releases/download/v${VERSION}/instar-v${VERSION}-x86_64-unknown-linux-gnu.tar.gz" \
  | sudo tar xz -C /usr/local/bin/
instar --help
```

The tarball contains the VMM and guest `.bin` files together; instar
finds them by looking in the directory containing the executable.

## System requirements

- **Linux** (instar uses KVM for sandboxed image processing)
- **KVM access**: `/dev/kvm` must be accessible
- Your user must be in the `kvm` group (`sudo usermod -aG kvm $USER`)

## Build from source

If you prefer to build from source, see the
[development guide](/components/instar/development/). This requires Docker and a
nightly Rust toolchain (handled automatically by the build container).
