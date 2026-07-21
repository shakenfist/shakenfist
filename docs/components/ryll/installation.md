# Installation

Pre-built packages are available from the
[GitHub Releases](https://github.com/shakenfist/ryll/releases) page
and as CI artifacts on pull requests.

## Debian / Ubuntu

Download the `.deb` package for your architecture and install. Both
`amd64` and `arm64` builds are published:

```bash
sudo dpkg -i ryll_0.1.0-1_amd64.deb   # or ryll_0.1.0-1_arm64.deb
sudo apt-get install -f   # install any missing dependencies
```

The package installs `ryll` to `/usr/bin/ryll`. Runtime dependencies
(libc, libssl) are detected automatically and will be pulled in by
`apt-get install -f` if missing.

## Red Hat / Fedora (RPM)

Download the `.rpm` package for your architecture and install. Both
`x86_64` and `aarch64` builds are published:

```bash
sudo dnf install ./ryll-0.1.0-1.x86_64.rpm   # or ryll-0.1.0-1.aarch64.rpm
```

Or with older `yum`-based systems:

```bash
sudo yum localinstall ryll-0.1.0-1.x86_64.rpm
```

The package installs `ryll` to `/usr/bin/ryll`. Runtime shared library
dependencies are detected automatically and resolved by your package
manager.

## macOS (Homebrew)

Ryll is available for Apple Silicon Macs via a Homebrew tap:

```bash
brew install shakenfist/tap/ryll
```

Or add the tap first, then install:

```bash
brew tap shakenfist/tap
brew install ryll
```

Only Apple Silicon (aarch64) is supported. Intel Macs are not
supported as Apple has dropped security updates for all x86 Macs.

Alternatively, download the tarball from
[GitHub Releases](https://github.com/shakenfist/ryll/releases)
and copy the binary to a directory on your `PATH`:

```bash
tar xzf ryll-0.1.0-aarch64-apple-darwin.tar.gz
cp ryll /usr/local/bin/
```

## Windows

Download the `.zip` archive for your architecture from
[GitHub Releases](https://github.com/shakenfist/ryll/releases)
(both `x86_64-pc-windows-msvc` and `aarch64-pc-windows-msvc` are
published), extract it, and run `ryll.exe`:

```powershell
Expand-Archive ryll-x86_64-pc-windows-msvc.zip -DestinationPath .
.\ryll.exe --help
```

To make it available system-wide, move `ryll.exe` to a directory
on your `PATH`.

Note that `--capture` mode is not available on Windows builds.

## pip (Python, Linux only)

`pip install ryll` (or `pip install shakenfist-client[vdi]`) installs a
per-architecture manylinux wheel with the compiled ryll GUI binary
already embedded — no runtime download, no cache, works offline
immediately after install. Wheels are published for Linux
`x86_64`/`aarch64` with glibc >= 2.28; other platforms should use one
of the packages above. See the
[README](/components/ryll/../README/#installing-via-pip) for the runtime system
library requirements.

## Building from source

If no pre-built package is available for your platform, you can build
ryll from source. See the [README](/components/ryll/../README/) for build instructions
and the [portability guide](/components/ryll/portability/) for platform-specific notes.

For macOS development and interactive testing, see the
[macOS development guide](/components/ryll/development-macos/).
