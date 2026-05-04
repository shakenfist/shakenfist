# Building Prototypes with Docker

This document describes how to build instar prototypes using Docker from the
command line, without requiring VSCode or the devcontainer extension.

## Prerequisites

- Docker installed and running
- Access to the instar repository

## Quick Start

Each prototype has a `.devcontainer/` directory with a Dockerfile. To build a
prototype:

```bash
cd /path/to/instar

# 1. Build the Docker image (one-time setup)
docker build -t <prototype>-builder prototypes/<prototype>/.devcontainer/

# 2. Run the build
docker run --rm \
  -v "$(pwd):/workspace" \
  -w /workspace/prototypes/<prototype> \
  -u "$(id -u):$(id -g)" \
  <prototype>-builder \
  ./build.sh
```

## Example: Building virtio-block3

```bash
cd /path/to/instar

# Build the Docker image
docker build -t virtio-block3-builder prototypes/virtio-block3/.devcontainer/

# Run the release build
docker run --rm \
  -v "$(pwd):/workspace" \
  -w /workspace/prototypes/virtio-block3 \
  -u "$(id -u):$(id -g)" \
  virtio-block3-builder \
  ./build.sh
```

This produces:
- `prototypes/virtio-block3/guest.bin` - The bare-metal guest binary
- `prototypes/virtio-block3/target/release/vmm` - The VMM executable

## Available Prototypes

| Prototype | Docker Image Name | Description |
|-----------|-------------------|-------------|
| helloworld | helloworld-builder | Minimal KVM hello world |
| helloworld2 | helloworld2-builder | Hello world with vm-memory |
| virtio-block | virtio-block-builder | Basic virtio-block file copy |
| virtio-block2 | virtio-block2-builder | Virtio-block with protobuf |
| virtio-block3 | virtio-block3-builder | Configurable sector sizes |

## Troubleshooting

### Permission Denied Errors

If you see "Permission denied" errors when building, the `target/` directory
may have files owned by root from a previous build. Clean it up:

```bash
# Remove target directory as root inside container
docker run --rm \
  -v "$(pwd):/workspace" \
  -w /workspace/prototypes/<prototype> \
  --user root \
  <prototype>-builder \
  rm -rf target

# Then retry the build
```

### User Mapping

The `-u "$(id -u):$(id -g)"` flag ensures files created in the container are
owned by your host user. This is important for avoiding permission issues.

### Cargo Cache

To speed up repeated builds, you can mount a cargo cache volume:

```bash
docker run --rm \
  -v "$(pwd):/workspace" \
  -v cargo-cache:/home/vscode/.cargo/registry \
  -w /workspace/prototypes/<prototype> \
  -u "$(id -u):$(id -g)" \
  <prototype>-builder \
  ./build.sh
```

## Running the VMM

After building, run the VMM with KVM access:

```bash
# Requires KVM access (root or kvm group membership)
sudo ./prototypes/virtio-block3/target/release/vmm \
  --input source.bin \
  --output dest.bin \
  prototypes/virtio-block3/guest.bin
```

## Using VSCode Devcontainers

Alternatively, open any prototype directory in VSCode and use "Reopen in
Container" (F1 → "Dev Containers: Reopen in Container"). The devcontainer
provides:

- Rust nightly toolchain
- Required components (rust-src, llvm-tools-preview)
- cargo-binutils for rust-objcopy
- protoc for Protocol Buffers
- KVM device access
