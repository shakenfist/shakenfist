# Binary Portability

This document explains how ryll binaries can be shared between machines
and across platforms.

## Supported Platforms

| Platform | GUI | Headless | Capture (`--capture`) |
|----------|-----|----------|----------------------|
| Linux x86_64 | Yes | Yes | Yes |
| macOS aarch64 (Apple Silicon) | Yes | Yes | Yes |
| Windows x86_64 | Yes | Yes | No (feature-gated off) |

Capture mode is disabled on Windows builds via a Cargo feature gate.
On Linux and macOS, it is enabled by default.

## Build Environment vs Runtime Environment

The devcontainer provides a consistent **build environment** - you can build ryll
on any machine with Docker installed, and get the same binary output. This is the
primary local development workflow.

For CI and release packaging, native builds on GitHub Actions runners are used
for each platform (see `.github/workflows/`).

For **running** the GUI client, you need to be on a machine with a display. The
devcontainer is primarily for:

- Building debug and release binaries
- Running linting (rustfmt, clippy)
- Running unit tests
- CI/CD pipelines
- Headless mode testing

## What's in the Binary

Rust binaries are partially static. Here's what's linked where:

### Statically Linked (built into the binary)
- All Rust code
- Most Rust dependencies
- No Rust runtime needed on target

### Dynamically Linked (required on target system)
- **glibc** - The C standard library
- **X11 libraries** - libxcb, libX11 (for windowing)
- **OpenGL libraries** - libGL, libEGL (for rendering)
- **Wayland libraries** - libwayland (if using Wayland)

## Compatibility

A binary built on one Linux system will run on another if:

| Requirement | Why |
|-------------|-----|
| Same or newer glibc | Backwards compatible, not forwards |
| Graphics stack installed | X11 or Wayland + OpenGL |
| Same architecture | x86_64 binary needs x86_64 system |

### Typical Compatibility Matrix

| Built On | Runs On |
|----------|---------|
| Debian 12 | Debian 12+, Ubuntu 22.04+, Fedora 37+ |
| Ubuntu 22.04 | Ubuntu 22.04+, Debian 12+, most modern distros |
| Older distro | More systems (older glibc = wider compatibility) |

### Checking Dependencies

To see what a binary requires:

```bash
ldd target/release/ryll
```

Example output:
```
linux-vdso.so.1
libxcb.so.1 => /lib/x86_64-linux-gnu/libxcb.so.1
libGL.so.1 => /lib/x86_64-linux-gnu/libGL.so.1
libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6
...
```

## Headless Mode

For headless mode (`--headless`), the binary still links against graphics
libraries at build time, but doesn't actually use them at runtime. This means:

- Headless mode works in containers
- Headless mode works on servers without displays
- The binary still needs the .so files present (or use `LD_LIBRARY_PATH` tricks)

## Cargo Features

Ryll uses a Cargo feature to control optional functionality:

| Feature | Default | Description |
|---------|---------|-------------|
| `capture` | Yes | Protocol capture (`--capture` flag, pcap + video). Requires `openh264`, `mp4`, `pcap-file`, `etherparse`. |

To build without capture support (e.g. for Windows):

```bash
cargo build --release --no-default-features
```

## Maximising Portability

If you need to distribute binaries widely:

1. **Build on older distro** - Use an older base image (e.g., Debian 11) for
   the devcontainer to get an older glibc

2. **AppImage packaging** - Bundle all dependencies into a single file

3. **Static musl build** - For headless-only use, you could build with musl
   for a fully static binary (doesn't work well with GUI)

## macOS

Ryll builds natively on macOS with Apple Silicon. eframe uses Metal and AppKit
backends — no X11 or OpenGL needed. Install Xcode Command Line Tools:

```bash
xcode-select --install
cargo build --release
```

## Windows

Ryll builds on Windows with MSVC. eframe uses Direct3D and WinAPI. Install
Visual Studio Build Tools, then:

```powershell
cargo build --release --no-default-features
```

The `--capture` flag is not available on Windows builds.

## Practical Workflow

For typical use within your own infrastructure:

```bash
# Build in container
make release

# Copy binary to target machine
scp target/release/ryll user@target:/usr/local/bin/

# Run on target (needs graphics libs installed)
ryll --file connection.vv
```

For automated testing:

```bash
# Run headless in container
docker run -v $(pwd):/workspace ryll-dev \
    /workspace/target/release/ryll --file test.vv --headless --cadence
```
