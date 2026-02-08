# Use Cases

This document covers common scenarios for using Occy Strap with practical
examples.

## Airgapped Environments

Transfer container images to systems without internet access.

### Download Images for Transfer

```bash
# Download multiple images to tarballs
occystrap process registry://docker.io/library/python:3.11 tar://python.tar
occystrap process registry://docker.io/library/nginx:latest tar://nginx.tar
occystrap process registry://docker.io/library/postgres:15 tar://postgres.tar
```

### Load Images on Airgapped System

```bash
# On the airgapped system with Docker
docker load -i python.tar
docker load -i nginx.tar
docker load -i postgres.tar
```

## Reproducible Builds

Create images with consistent hashes regardless of build time.

### Normalize Timestamps

```bash
# Download with timestamp normalization (Unix epoch)
occystrap process registry://docker.io/library/busybox:latest \
    tar://busybox.tar -f normalize-timestamps

# Verify hash is consistent
sha256sum busybox.tar
# Running again produces the same hash
```

### Use Specific Timestamp

```bash
# Normalize to a specific date (Jan 1, 2024)
occystrap process registry://docker.io/library/python:3.11 \
    tar://python.tar -f "normalize-timestamps:ts=1704067200"
```

## Container Forensics

Inspect and analyze container image contents.

### Search for Configuration Files

```bash
# Find all .conf files
occystrap search registry://docker.io/library/nginx:latest "*.conf"

# Find all Python files with regex
occystrap search --regex docker://myapp:v1 ".*\.py$"

# Machine-readable output for scripting
occystrap search --script-friendly tar://image.tar "*.sh" > shell_files.txt
```

### Extract and Inspect Layers

```bash
# Extract with expanded layers for inspection
occystrap process registry://docker.io/library/python:3.11 \
    "dir://python-inspect?expand=true"

# Browse the extracted filesystem
ls -la python-inspect/
find python-inspect -name "*.conf"
```

### Search While Processing

```bash
# Search for config files while creating tarball
occystrap process registry://docker.io/library/nginx:latest \
    tar://nginx.tar -f "search:pattern=etc/**/*.conf"
```

## Private Registry Operations

Work with authenticated registries.

### Download from Private Registry

```bash
# Using command-line options
occystrap --username myuser --password mytoken \
    process registry://registry.gitlab.com/mygroup/myimage:latest \
    tar://myimage.tar

# Using environment variables (more secure)
export OCCYSTRAP_USERNAME=myuser
export OCCYSTRAP_PASSWORD=mytoken
occystrap process registry://registry.gitlab.com/mygroup/myimage:latest \
    tar://myimage.tar
```

### Mirror Images Between Registries

```bash
# Copy from Docker Hub to private registry
occystrap --username destuser --password desttoken \
    process registry://docker.io/library/nginx:latest \
    registry://myregistry.example.com/mirror/nginx:latest

# Copy from local Docker to registry
occystrap --username myuser --password mytoken \
    process docker://myapp:v1 \
    registry://ghcr.io/myorg/myapp:v1
```

## Multi-Architecture Images

Work with images for different CPU architectures.

### Download ARM64 Image

```bash
# Using global options
occystrap --os linux --architecture arm64 --variant v8 \
    process registry://docker.io/library/busybox:latest \
    tar://busybox-arm64.tar

# Using URI query parameters
occystrap process \
    "registry://docker.io/library/busybox:latest?os=linux&arch=arm64&variant=v8" \
    tar://busybox-arm64.tar
```

### Download Multiple Architectures

```bash
# AMD64
occystrap process registry://docker.io/library/python:3.11 \
    tar://python-amd64.tar

# ARM64
occystrap --architecture arm64 --variant v8 \
    process registry://docker.io/library/python:3.11 \
    tar://python-arm64.tar
```

## Storage Optimization

Reduce disk usage when working with multiple images.

### Shared Layer Storage

```bash
# Download multiple images with layer deduplication
occystrap process registry://docker.io/library/python:3.11 \
    "dir://shared-images?unique_names=true"

occystrap process registry://docker.io/library/python:3.10 \
    "dir://shared-images?unique_names=true"

occystrap process registry://docker.io/library/python:3.9 \
    "dir://shared-images?unique_names=true"

# Shared base layers are stored only once
ls -la shared-images/
cat shared-images/catalog.json
```

### Clean Up Images

```bash
# Exclude unnecessary files to reduce size
occystrap process registry://docker.io/library/python:3.11 \
    tar://python-clean.tar \
    -f "exclude:pattern=**/__pycache__/**,**/*.pyc,**/.git/**"
```

### Efficient Registry Pushes with Deduplication

When pushing images that share base layers, Occy Strap skips uploading
blobs that already exist in the target registry. Combining this with
`normalize-timestamps` maximizes the chance of layer deduplication:

```bash
# Push multiple Python versions -- shared base layers upload once
for version in 3.9 3.10 3.11; do
    occystrap process \
        "registry://docker.io/library/python:$version" \
        "registry://myregistry.example.com/python:$version" \
        -f normalize-timestamps
done
```

Compression is deterministic (gzip suppresses header timestamps, zstd
is inherently deterministic), so identical layers always produce the
same compressed digest. The second and third pushes will skip uploading
any layers shared with earlier pushes.

## OCI Runtime Integration

Create runtime bundles for runc.

### Generate OCI Bundle

```bash
# Create OCI bundle from registry image
occystrap process registry://docker.io/library/hello-world:latest \
    oci://hello-bundle

# Run with runc
cd hello-bundle
sudo runc run hello-world
```

### From Local Docker Image

```bash
# Export and convert to OCI bundle
occystrap process docker://myapp:v1 oci://myapp-bundle

# Run the bundle
cd myapp-bundle
sudo runc run myapp
```

## Podman Integration

Work with Podman instead of Docker.

### Enable Podman Socket

```bash
# Start rootless Podman socket
systemctl --user start podman.socket

# Or rootful
sudo systemctl start podman.socket
```

### Fetch from Podman

```bash
# Rootless Podman
occystrap process \
    "docker://myimage:v1?socket=/run/user/$(id -u)/podman/podman.sock" \
    tar://myimage.tar

# Rootful Podman
occystrap process \
    "docker://myimage:v1?socket=/run/podman/podman.sock" \
    tar://myimage.tar
```

### Load into Podman

```bash
# Load tarball into Podman
occystrap process tar://myimage.tar \
    "docker://myimage:v1?socket=/run/user/$(id -u)/podman/podman.sock"
```

## CI/CD Pipelines

Integrate Occy Strap into build pipelines.

### Cache Images for CI

```bash
#!/bin/bash
# ci-image-cache.sh - Download images for CI environment

CACHE_DIR="/var/cache/ci-images"
mkdir -p "$CACHE_DIR"

# Download with normalized timestamps for consistent caching
for image in python:3.11 node:18 postgres:15; do
    name=$(echo "$image" | tr ':' '-')
    if [ ! -f "$CACHE_DIR/$name.tar" ]; then
        occystrap process "registry://docker.io/library/$image" \
            "tar://$CACHE_DIR/$name.tar" -f normalize-timestamps
    fi
done
```

### Verify Image Contents

```bash
#!/bin/bash
# verify-image.sh - Check image doesn't contain sensitive files

IMAGE="$1"
SENSITIVE_PATTERNS="*.pem *.key id_rsa* .env* secrets*"

for pattern in $SENSITIVE_PATTERNS; do
    matches=$(occystrap search --script-friendly \
        "registry://$IMAGE" "$pattern" 2>/dev/null)
    if [ -n "$matches" ]; then
        echo "WARNING: Found sensitive files matching '$pattern':"
        echo "$matches"
        exit 1
    fi
done

echo "Image passed security check"
```

## Debugging

Troubleshoot issues with verbose output.

### Enable Debug Logging

```bash
occystrap --verbose process registry://docker.io/library/busybox:latest \
    tar://busybox.tar
```

### Inspect Layer Contents

```bash
# Extract layers without merging
occystrap process registry://docker.io/library/python:3.11 dir://python-layers

# Each layer is a separate tarball
ls python-layers/*.tar

# Inspect a specific layer
tar -tvf python-layers/<layer-hash>.tar | head -50
```
