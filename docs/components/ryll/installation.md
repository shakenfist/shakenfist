# Installation

Pre-built packages are available from the
[GitHub Releases](https://github.com/shakenfist/ryll/releases) page
and as CI artifacts on pull requests.

## Debian / Ubuntu

Download the `.deb` package for your architecture and install:

```bash
sudo dpkg -i ryll_0.1.0-1_amd64.deb
sudo apt-get install -f   # install any missing dependencies
```

The package installs `ryll` to `/usr/bin/ryll`. Runtime dependencies
(libc, libssl) are detected automatically and will be pulled in by
`apt-get install -f` if missing.

## Red Hat / Fedora (RPM)

Download the `.rpm` package for your architecture and install:

```bash
sudo dnf install ./ryll-0.1.0-1.x86_64.rpm
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

Download the `.zip` archive from
[GitHub Releases](https://github.com/shakenfist/ryll/releases),
extract it, and run `ryll.exe`:

```powershell
Expand-Archive ryll-x86_64-pc-windows-msvc.zip -DestinationPath .
.\ryll.exe --help
```

To make it available system-wide, move `ryll.exe` to a directory
on your `PATH`.

Note that `--capture` mode is not available on Windows builds.

## Building from source

If no pre-built package is available for your platform, you can build
ryll from source. See the [README](/components/ryll/../README/) for build instructions
and the [portability guide](/components/ryll/portability/) for platform-specific notes.
