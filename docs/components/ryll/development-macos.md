# Building and Testing on macOS

This guide explains how to set up a local development environment on
macOS so you can build and test ryll interactively without going
through the Homebrew release cycle.

## Prerequisites

### Xcode Command Line Tools

The Xcode command line tools provide the C compiler and linker that
Rust needs. If you haven't already installed them:

```bash
xcode-select --install
```

### Rust toolchain

Install Rust via [rustup](https://rustup.rs/):

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

Follow the prompts to install the `stable` toolchain. After
installation, ensure `~/.cargo/bin` is on your `PATH` (the installer
usually adds this to your shell profile).

Verify:

```bash
rustc --version
cargo --version
```

### No additional system libraries required

Unlike Linux (which needs X11, Wayland, and OpenGL development
libraries), macOS builds use native Metal and AppKit backends via
eframe. Everything you need comes with the Xcode Command Line Tools.

## Clone the repository

```bash
git clone https://github.com/shakenfist/ryll.git
cd ryll
```

Or, if you already have a checkout, just `cd` into it.

## Building

### Debug build (fast compile, slow runtime)

```bash
cargo build
```

The binary lands at `target/debug/ryll`.

### Release build (slow compile, optimised runtime)

```bash
cargo build --release
```

The binary lands at `target/release/ryll`.

To match the CI deployment target (macOS 14 Sonoma and newer):

```bash
MACOSX_DEPLOYMENT_TARGET=14.0 cargo build --release
```

This is only necessary if you plan to distribute the binary to other
machines. For local testing it makes no difference.

## Running interactively

The quickest way during development is `cargo run`, which builds and
runs in one step:

```bash
# Debug build (faster compile)
cargo run -- --file /path/to/connection.vv

# Release build (faster runtime)
cargo run --release -- --file /path/to/connection.vv

# Direct connection
cargo run -- --direct 192.168.1.100:5900

# Headless with cadence mode
cargo run -- --file connection.vv --headless --cadence -v
```

Or run the binary directly:

```bash
./target/release/ryll --file connection.vv
```

Note: everything after `--` is passed to ryll, not to cargo.

## Running tests

```bash
cargo test
```

## Linting

To match what CI and the pre-commit hooks check:

```bash
# Check formatting
cargo fmt --check

# Auto-fix formatting
cargo fmt

# Run clippy
cargo clippy -- -D warnings
```

If you want to use the project's pre-commit hooks:

```bash
pip install pre-commit   # or: brew install pre-commit
pre-commit install
```

Note: the pre-commit hooks run rustfmt and clippy inside Docker,
which will pull and build a container image on first use. If you
prefer to run them natively (as shown above), that works too -- CI
runs native cargo, not Docker.

## Iterative development workflow

A typical edit-build-test cycle on macOS:

```bash
# 1. Make your changes

# 2. Check formatting and lint
cargo fmt && cargo clippy -- -D warnings

# 3. Run tests
cargo test

# 4. Build and run interactively
cargo run -- --file connection.vv -v
```

For faster iteration, use the debug build (`cargo run` without
`--release`). Compile times are significantly shorter. Switch to
`--release` when you need to test performance or match production
behaviour.

### Verbose logging

Add `-v` to write detailed logs to `/tmp/ryll.log`:

```bash
cargo run -- --file connection.vv -v
```

### Capture mode

Record protocol traffic and display video for debugging:

```bash
cargo run -- --file connection.vv --capture /tmp/ryll-capture
```

This writes per-channel pcap files and an MP4 video to the capture
directory. See [diagnostics.md](/components/ryll/diagnostics/) for details on
what is captured.

## Differences from the devcontainer workflow

The devcontainer and `Makefile` targets (`make build`, `make lint`,
etc.) run everything inside a Docker container, which is useful for
consistent Linux builds but cannot run the macOS GUI. On macOS,
building natively with `cargo` is the right approach.

| Task | Devcontainer (Linux) | Native macOS |
|------|---------------------|--------------|
| Debug build | `make build` | `cargo build` |
| Release build | `make release` | `cargo build --release` |
| Tests | `make test` | `cargo test` |
| Lint | `make lint` | `cargo fmt --check && cargo clippy -- -D warnings` |
| Lint + fix | `make lint-fix` | `cargo fmt` |
| Run GUI | Not possible (no display) | `cargo run -- --file ...` |

## Troubleshooting

### Linker errors about missing frameworks

Ensure Xcode Command Line Tools are installed:

```bash
xcode-select --install
```

If you have multiple Xcode versions, make sure the active one is
correct:

```bash
xcode-select -p
```

### openh264 and mozjpeg build issues

`openh264-sys2` and `mozjpeg-sys` -- pulled in via
`shakenfist-spice-compression` and `shakenfist-spice-renderer` (see
[Key dependencies](/components/ryll/development/#key-dependencies)) -- compile
vendored C/C++ codec sources at build time using the [`cc`
crate](https://docs.rs/cc); they do not download a prebuilt library,
so a broken build here is never a network problem. The compiler
they need is the same one the "Xcode Command Line Tools" step above
installs, so on a correctly set-up machine this just works.

Both dependencies are also unconditional: neither is behind a Cargo
feature, so `cargo build --no-default-features` still compiles both
of them. There is no flag that skips this step -- if the compiler is
broken, the fix is the compiler, not the feature set.

If the build fails inside either crate's compile step, check that
the Xcode Command Line Tools are actually active:

```bash
xcode-select -p              # should print a valid path
sudo xcodebuild -license accept
```

An error here shows up as a C/C++ compiler failure (missing `cc`,
license not accepted, or a stale/uninstalled toolchain), not the
generic linker errors covered above.

NASM is not required by either crate: both fall back to a slower,
non-SIMD codec path if `nasm` isn't on `PATH`. On Apple Silicon this
never matters (aarch64 doesn't use NASM's assembly path at all); on
an Intel Mac, `brew install nasm` picks up the faster path if you
want it.

### Rust toolchain updates

Keep your toolchain current to match CI:

```bash
rustup update stable
```
