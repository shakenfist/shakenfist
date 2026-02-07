# Command Reference

This document provides a complete reference for Occy Strap's command-line
interface.

## Global Options

These options apply to all commands and must be specified before the command
name:

| Option | Environment Variable | Description |
|--------|---------------------|-------------|
| `--verbose` | | Enable debug logging |
| `--os OSNAME` | | Target operating system (default: linux) |
| `--architecture ARCH` | | Target CPU architecture (default: amd64) |
| `--variant VARIANT` | | CPU variant (e.g., v8 for ARM) |
| `--username USER` | `OCCYSTRAP_USERNAME` | Registry authentication username |
| `--password PASS` | `OCCYSTRAP_PASSWORD` | Registry authentication password |
| `--insecure` | | Use HTTP instead of HTTPS for registries |
| `--compression TYPE` | `OCCYSTRAP_COMPRESSION` | Layer compression for registry output (gzip, zstd) |

Example:

```bash
occystrap --verbose --architecture arm64 --variant v8 \
    process registry://docker.io/library/busybox:latest tar://busybox.tar
```

## Commands

### process

The primary command for processing container images through the pipeline.

```bash
occystrap process SOURCE DESTINATION [-f FILTER]...
```

**Arguments:**

- `SOURCE` - Input URI specifying where to read the image from
- `DESTINATION` - Output URI specifying where to write the image to
- `-f FILTER` - Optional filter(s) to apply (can be specified multiple times)

**Examples:**

```bash
# Registry to tarball
occystrap process registry://docker.io/library/busybox:latest tar://busybox.tar

# Registry to directory with filters
occystrap process registry://docker.io/library/python:3.11 dir://python \
    -f normalize-timestamps -f "exclude:pattern=**/__pycache__/**"

# Docker daemon to registry
occystrap process docker://myimage:v1 registry://myregistry.com/myimage:v1
```

### search

Search for files within container image layers.

```bash
occystrap search SOURCE PATTERN [--regex] [--script-friendly]
```

**Arguments:**

- `SOURCE` - Input URI specifying the image to search
- `PATTERN` - Glob pattern or regex to match against file paths

**Options:**

- `--regex` - Treat PATTERN as a regular expression instead of a glob
- `--script-friendly` - Output in machine-parseable format

**Examples:**

```bash
# Search for shell binaries
occystrap search registry://docker.io/library/busybox:latest "bin/*sh"

# Search with regex
occystrap search --regex docker://python:3.11 ".*\.py$"

# Machine-parseable output
occystrap search --script-friendly tar://image.tar "*.conf"
```

## Input URI Schemes

### registry://

Fetch images from Docker/OCI registries via HTTP API.

```
registry://[user:pass@]HOST/IMAGE:TAG[?options]
```

**Query Options:**

| Option | Description |
|--------|-------------|
| `arch=ARCH` | CPU architecture (overrides global) |
| `os=OS` | Operating system (overrides global) |
| `variant=VAR` | CPU variant (overrides global) |
| `insecure=true` | Use HTTP instead of HTTPS |

**Examples:**

```bash
# Docker Hub
registry://docker.io/library/busybox:latest
registry://registry-1.docker.io/library/python:3.11

# GitHub Container Registry
registry://ghcr.io/myorg/myimage:v1

# Private registry
registry://myregistry.example.com/myproject/myimage:latest

# With architecture selection
registry://docker.io/library/busybox:latest?os=linux&arch=arm64&variant=v8
```

### docker://

Fetch images from the local Docker or Podman daemon.

```
docker://IMAGE:TAG[?socket=/path/to/socket]
```

**Query Options:**

| Option | Description |
|--------|-------------|
| `socket=/path` | Custom daemon socket path |

**Examples:**

```bash
# Docker daemon (default socket)
docker://myimage:v1

# Podman (rootful)
docker://myimage:v1?socket=/run/podman/podman.sock

# Podman (rootless)
docker://myimage:v1?socket=/run/user/1000/podman/podman.sock
```

### tar://

Read images from docker-save format tarballs.

```
tar:///path/to/file.tar
```

**Examples:**

```bash
tar:///home/user/images/busybox.tar
tar://./local-image.tar
```

## Output URI Schemes

### tar://

Create docker-loadable tarballs (v1.2 format).

```
tar:///path/to/output.tar
```

The resulting tarball can be loaded with `docker load -i output.tar`.

### dir://

Extract images to a directory.

```
dir:///path/to/directory[?options]
```

**Query Options:**

| Option | Description |
|--------|-------------|
| `unique_names=true` | Enable layer deduplication across images |
| `expand=true` | Expand layer tarballs to filesystem |

**Examples:**

```bash
# Simple extraction
dir://./extracted

# With layer deduplication (for multiple images)
dir://./shared?unique_names=true

# Expanded layers for inspection
dir://./inspect?expand=true
```

### oci://

Create OCI runtime bundles for use with runc.

```
oci:///path/to/bundle
```

The bundle can be run with `runc run <container-id>`.

### mounts://

Create overlay mount-based extraction using extended attributes.

```
mounts:///path/to/directory
```

### docker://

Load images into the local Docker or Podman daemon.

```
docker://IMAGE:TAG[?socket=/path/to/socket]
```

**Examples:**

```bash
# Load into Docker
docker://myimage:v1

# Load into Podman
docker://myimage:v1?socket=/run/podman/podman.sock
```

### registry://

Push images to Docker/OCI registries.

```
registry://HOST/IMAGE:TAG[?insecure=true&compression=TYPE]
```

**Query Options:**

| Option | Description |
|--------|-------------|
| `insecure=true` | Use HTTP instead of HTTPS |
| `compression=TYPE` | Layer compression: gzip (default) or zstd |

**Examples:**

```bash
# Push to private registry
registry://myregistry.example.com/myproject/myimage:v1

# Push with insecure (HTTP)
registry://internal.local/image:tag?insecure=true

# Push with zstd compression (requires Docker 20.10+ or containerd 1.5+)
registry://myregistry.example.com/myimage:v1?compression=zstd
```

## Filters

Filters transform or inspect image elements as they pass through the pipeline.
Multiple filters can be chained using multiple `-f` options.

### normalize-timestamps

Normalize file modification times in layer tarballs for reproducible builds.

```
normalize-timestamps
normalize-timestamps:ts=TIMESTAMP
```

**Options:**

| Option | Description |
|--------|-------------|
| `ts=TIMESTAMP` | Unix timestamp to use (default: 0, Unix epoch) |

When timestamps are normalized, layer SHA256 hashes are recalculated and the
manifest is updated.

**Examples:**

```bash
# Normalize to Unix epoch
-f normalize-timestamps

# Normalize to specific timestamp (Jan 1, 2021)
-f "normalize-timestamps:ts=1609459200"
```

### search

Search for files matching a pattern while processing.

```
search:pattern=PATTERN[,regex=true][,script_friendly=true]
```

**Options:**

| Option | Description |
|--------|-------------|
| `pattern=PATTERN` | Glob or regex pattern to match |
| `regex=true` | Treat pattern as regex instead of glob |
| `script_friendly=true` | Machine-parseable output format |

When used as a filter, search prints matches AND passes elements to the output.

**Examples:**

```bash
# Search while creating tarball
-f "search:pattern=*.conf"

# Search with regex
-f "search:pattern=.*\.py$,regex=true"
```

### inspect

Record layer metadata to a JSONL file. This is a passthrough
filter that does not modify the image data -- it only observes
and records. Place it between other filters to measure their
effect on layer digests and sizes.

```
inspect:file=PATH
```

**Options:**

| Option | Description |
|--------|-------------|
| `file=PATH` | Path to the JSONL output file (required) |

Each invocation appends one JSON line containing the image
name, tag, layer digests, sizes, and build history. Multiple
images can be recorded to the same file.

**Examples:**

```bash
# Record layer metadata before and after normalization
-f "inspect:file=before.jsonl" \
-f normalize-timestamps \
-f "inspect:file=after.jsonl"

# Full pipeline with three observation points
-f "inspect:file=as-built.jsonl" \
-f normalize-timestamps \
-f "inspect:file=post-normalize.jsonl" \
-f "exclude:pattern=**/.git" \
-f "inspect:file=post-exclude.jsonl"
```

### exclude

Exclude files matching glob patterns from image layers.

```
exclude:pattern=PATTERN[,PATTERN2,...]
```

Files matching the patterns are removed from layers. Layer hashes are
recalculated after modification.

**Examples:**

```bash
# Exclude git directories
-f "exclude:pattern=**/.git/**"

# Exclude multiple patterns
-f "exclude:pattern=**/.git/**,**/__pycache__/**,**/*.pyc"
```

## Legacy Commands

The following commands are deprecated but still available for backwards
compatibility. Use the `process` and `search` commands instead.

| Legacy Command | New Equivalent |
|----------------|----------------|
| `fetch-to-tarfile REG IMG TAG FILE` | `process registry://REG/IMG:TAG tar://FILE` |
| `fetch-to-extracted REG IMG TAG DIR` | `process registry://REG/IMG:TAG dir://DIR` |
| `fetch-to-oci REG IMG TAG DIR` | `process registry://REG/IMG:TAG oci://DIR` |
| `fetch-to-mounts REG IMG TAG DIR` | `process registry://REG/IMG:TAG mounts://DIR` |
| `tarfile-to-extracted FILE DIR` | `process tar://FILE dir://DIR` |
| `docker-to-tarfile IMG TAG FILE` | `process docker://IMG:TAG tar://FILE` |
| `docker-to-extracted IMG TAG DIR` | `process docker://IMG:TAG dir://DIR` |
| `docker-to-oci IMG TAG DIR` | `process docker://IMG:TAG oci://DIR` |
| `search-layers REG IMG TAG PAT` | `search registry://REG/IMG:TAG PAT` |
| `search-layers-tarfile FILE PAT` | `search tar://FILE PAT` |
| `search-layers-docker IMG TAG PAT` | `search docker://IMG:TAG PAT` |
| `recreate-image DIR IMG TAG FILE` | `process dir://DIR tar://FILE` |
