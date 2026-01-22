# Installation

This guide covers installing Occy Strap and verifying it's working correctly.

## Requirements

- Python 3.7 or later
- pip (Python package manager)
- Network access to container registries (for downloading images)
- Optionally: Docker or Podman daemon (for local image operations)

## Installation from PyPI

The simplest way to install Occy Strap is via pip:

```bash
pip install occystrap
```

## Installation from Source

To install the latest development version:

```bash
git clone https://github.com/shakenfist/occystrap.git
cd occystrap
pip install -e .
```

## Verifying the Installation

After installation, verify that Occy Strap is working:

```bash
# Check the version
occystrap --help

# Try downloading a small image
occystrap process registry://docker.io/library/hello-world:latest \
    tar://hello-world.tar

# Verify the tarball was created
ls -la hello-world.tar
```

## Dependencies

Occy Strap depends on the following Python packages (installed automatically):

| Package | Purpose |
|---------|---------|
| click | Command-line interface framework |
| requests | HTTP client for registry API |
| requests-unixsocket | Unix socket support for Docker daemon |
| prettytable | Formatted table output |
| oslo.concurrency | Concurrency utilities |
| shakenfist-utilities | Shared utilities |

## Platform Support

Occy Strap is primarily designed for Linux systems. It may work on other
platforms with Python support, but has been tested on:

- Ubuntu 20.04, 22.04
- CentOS 7, 8
- Debian 10, 11

## Optional: Docker/Podman Socket Access

To use Occy Strap with local Docker or Podman images, you need access to the
daemon socket:

### Docker

The Docker socket is typically at `/var/run/docker.sock`. Your user needs to be
in the `docker` group:

```bash
sudo usermod -aG docker $USER
# Log out and back in for group membership to take effect
```

### Podman

Podman doesn't run a daemon by default. Enable the socket service:

```bash
# For rootless Podman (recommended)
systemctl --user start podman.socket

# For rootful Podman
sudo systemctl start podman.socket
```

The rootless socket is at `/run/user/<uid>/podman/podman.sock`, and the rootful
socket is at `/run/podman/podman.sock`.

## Troubleshooting

### Permission Denied on Docker Socket

If you see "Permission denied" errors when accessing the Docker socket:

```bash
# Add your user to the docker group
sudo usermod -aG docker $USER

# Log out and back in, or use newgrp
newgrp docker
```

### SSL Certificate Errors

If you encounter SSL certificate verification errors with private registries:

```bash
# Use the --insecure flag
occystrap --insecure process registry://myregistry.local/image:tag tar://out.tar
```

### Connection Refused to Registry

Ensure the registry hostname is correct and network access is available:

```bash
# Test connectivity
curl -v https://registry-1.docker.io/v2/
```

## Next Steps

- [Command Reference](/components/occystrap/command-reference/) - Learn the CLI commands
- [Use Cases](/components/occystrap/use-cases/) - Common scenarios and examples
