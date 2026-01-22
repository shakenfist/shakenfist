# Occy Strap Documentation

Occy Strap is a Docker/OCI container image manipulation toolkit that allows you
to work with container images without requiring Docker to be installed. It
follows a flexible **input -> filter -> output** pipeline pattern for processing
container images.

## What Can Occy Strap Do?

- **Download images** from Docker registries, local Docker/Podman daemons, or
  existing tarballs
- **Transform images** using filters (normalize timestamps, exclude files,
  search for content)
- **Export images** to tarballs, directories, OCI runtime bundles, or push to
  registries
- **Search images** for files matching glob or regex patterns

## Quick Example

```bash
# Download an image from Docker Hub to a tarball
occystrap process registry://docker.io/library/busybox:latest tar://busybox.tar

# Search for shell binaries in an image
occystrap search registry://docker.io/library/busybox:latest "bin/*sh"

# Download with timestamp normalization for reproducible builds
occystrap process registry://docker.io/library/python:3.11 tar://python.tar \
    -f normalize-timestamps
```

## Documentation Index

### Getting Started

- [Installation](/components/occystrap/installation/) - How to install Occy Strap and verify it's
  working

### Reference

- [Command Reference](/components/occystrap/command-reference/) - Complete CLI commands, options,
  URI schemes, and filters
- [Pipeline Architecture](/components/occystrap/pipeline/) - Understanding inputs, filters, and
  outputs

### Guides

- [Use Cases](/components/occystrap/use-cases/) - Common scenarios and examples

## Key Concepts

### URI-Style Commands

Occy Strap uses URI-style arguments for specifying sources and destinations:

```bash
occystrap process SOURCE DESTINATION [-f FILTER]...
```

Input URIs specify where to get images:
- `registry://docker.io/library/busybox:latest` - Docker/OCI registry
- `docker://myimage:v1` - Local Docker daemon
- `tar:///path/to/image.tar` - Existing tarball

Output URIs specify where to write images:
- `tar:///path/to/output.tar` - Docker-loadable tarball
- `dir:///path/to/directory` - Extracted directory
- `oci:///path/to/bundle` - OCI runtime bundle
- `registry://myregistry.com/image:tag` - Push to registry

### Pipeline Pattern

```
Input Source  -->  Filter Chain (optional)  -->  Output Writer  -->  Files
```

Filters transform or inspect image elements as they flow through the pipeline.
Multiple filters can be chained together.

## Requirements

- Python 3.6+
- No Docker installation required (works independently)
- For registry access: network connectivity to the registry
- For local Docker access: running Docker daemon with accessible socket

## Links

- [GitHub Repository](https://github.com/shakenfist/occystrap)
- [Shaken Fist Project](https://shakenfist.com)
