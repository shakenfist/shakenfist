# Command Reference

This document provides a complete reference for Occy Strap's command-line
interface.

## Global Options

These options apply to all commands and must be specified before the command
name:

| Option | Environment Variable | Description |
|--------|---------------------|-------------|
| `--verbose` | | Enable debug logging for occystrap modules |
| `--debug` | | Enable debug logging for all modules (includes library output) |
| `--os OSNAME` | | Target operating system (default: linux) |
| `--architecture ARCH` | | Target CPU architecture (default: amd64) |
| `--variant VARIANT` | | CPU variant (e.g., v8 for ARM) |
| `--username USER` | `OCCYSTRAP_USERNAME` | Registry authentication username |
| `--password PASS` | `OCCYSTRAP_PASSWORD` | Registry authentication password |
| `--insecure` | | Use HTTP instead of HTTPS for registries |
| `--compression TYPE` | `OCCYSTRAP_COMPRESSION` | Layer compression for registry output (gzip, zstd) |
| `--parallel N`, `-j N` | `OCCYSTRAP_PARALLEL` | Number of parallel download/upload threads (default: 4) |
| `--temp-dir PATH` | `OCCYSTRAP_TEMP_DIR` | Directory for temporary files (default: system temp) |
| `--layer-cache PATH` | `OCCYSTRAP_LAYER_CACHE` | JSON file for cross-invocation layer caching |
| `-O`, `--output-format` | | Output format for `info`/`check`: `text` (default) or `json` |

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

### info

Display information about a container image without downloading layer
blobs. Shows metadata from the manifest and config: architecture, OS,
layer count, compressed sizes, compression formats, history entries,
labels, environment variables, entrypoint/cmd, and more.

```bash
occystrap info SOURCE
```

**Arguments:**

- `SOURCE` - Input URI specifying the image to inspect

**Output format** is controlled by the global `-O` / `--output-format`
option (`text` or `json`).

**What's shown depends on the input source:**

| Source | Manifest data | Config data |
|--------|--------------|-------------|
| `registry://` | Yes (compressed sizes, mediaTypes) | Yes |
| `docker://` | No | Yes |
| `tar://` | No | Yes |
| `dockerpush://` | No | No |

**Examples:**

```bash
# Human-readable output from registry
occystrap info registry://docker.io/library/busybox:latest

# JSON output
occystrap -O json info registry://docker.io/library/busybox:latest

# From local Docker daemon
occystrap info docker://myimage:v1

# From tarball
occystrap info tar://image.tar

# Specific architecture
occystrap --architecture arm64 --variant v8 \
    info registry://docker.io/library/busybox:latest
```

### proxy

Run a persistent filtering registry proxy that receives Docker pushes,
applies filters, and forwards images to a downstream registry. The proxy
runs until interrupted (Ctrl+C or SIGTERM).

```bash
occystrap proxy --downstream REGISTRY [-f FILTER]... [--listen HOST:PORT] [--concurrency N]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--downstream REGISTRY`, `-d` | Downstream registry host (required, e.g., `ghcr.io/myorg`) |
| `--upstream REGISTRY`, `-u` | Upstream registry for pull-through (e.g., `docker.io`, `user:pass@registry.example.com`) |
| `--listen HOST:PORT`, `-l` | Listen address (default: `127.0.0.1:5050`) |
| `-f FILTER` | Filter(s) to apply (can be specified multiple times) |
| `--concurrency N`, `-c` | Max concurrent image processing (default: 4) |

The proxy also respects global options `--layer-cache`, `--temp-dir`,
`--username`, `--password`, `--insecure`, `--compression`, and
`--parallel`.

**Behavior:**

- The proxy implements the Docker Registry V2 push-path API
- When a manifest is received, the proxy blocks the HTTP response
  while processing the image through the filter pipeline and pushing
  to the downstream registry
- Multiple images are processed concurrently (limited by `--concurrency`)
- Docker sees 201 on success and 500 on failure
- Repository names from the push are passed through to the downstream
  registry as-is (e.g., `localhost:5050/kolla/nova-api:latest` becomes
  `REGISTRY/kolla/nova-api:latest`)
- Manifest lists (multi-arch) are rejected (single-platform only)
- On shutdown, the proxy waits for in-flight processing to complete
  (up to 5 minutes), saves the layer cache, and cleans up temporary
  files
- When `--upstream` is specified, the proxy also serves pull requests:
  - `GET /v2/{name}/manifests/{ref}` checks the downstream registry
    first; on a miss, fetches from upstream, applies filters, pushes
    to downstream, then serves the result
  - `GET /v2/{name}/blobs/sha256:{digest}` proxies blobs from
    downstream
  - `HEAD` requests check downstream for existence
  - Upstream credentials can be embedded: `user:pass@host`
  - Per-image locks prevent duplicate upstream fetches for concurrent
    requests

**Examples:**

```bash
# Start proxy with timestamp normalization
occystrap proxy --listen 127.0.0.1:5050 \
    --downstream ghcr.io/myorg \
    -f normalize-timestamps

# Start with layer cache for cross-image dedup
occystrap proxy --listen 127.0.0.1:5050 \
    --downstream ghcr.io/myorg \
    --layer-cache /tmp/layer-cache.json \
    -f normalize-timestamps

# Push to the proxy from Docker
docker tag myimage:latest localhost:5050/myimage:latest
docker push localhost:5050/myimage:latest

# Use with kolla-build
kolla-build --registry 127.0.0.1:5050 --push ...

# Pull-through proxy with Docker Hub as upstream
occystrap proxy --listen 127.0.0.1:5050 \
    --downstream ghcr.io/myorg \
    --upstream docker.io \
    -f normalize-timestamps

# Pull through the proxy
docker pull localhost:5050/library/busybox:latest

# Pull-through with authenticated upstream
occystrap proxy --listen 127.0.0.1:5050 \
    --downstream ghcr.io/myorg \
    --upstream user:token@registry.example.com
```

### check

Check validity of a container image. Validates structural integrity,
history consistency, compression compatibility, and filesystem
correctness. Use `--fast` to skip layer downloads and only check
metadata consistency.

```bash
occystrap check SOURCE [--fast]
```

**Arguments:**

- `SOURCE` - Input URI specifying the image to check

**Options:**

- `--fast` - Skip layer download, check metadata only (manifest and
  config consistency)

**Output format** is controlled by the global `-O` / `--output-format`
option (`text` or `json`).

**Exit code** is non-zero if any errors are found (useful for CI).

**Fast mode checks** (metadata only, no layer download):

| Check | Description |
|-------|-------------|
| Schema version | `schemaVersion` must be 2 |
| Rootfs type | `rootfs.type` must be `"layers"` |
| Layer count | Manifest layer count must match config diff_id count |
| Config descriptor | Manifest must have a config descriptor |
| History count | Non-empty history entries must equal layer count |
| zstd compatibility | Warns if zstd layers present (needs Docker 20.10+) |
| Media type info | Reports OCI vs Docker v2 media types |
| Large layers | Warns if any layer exceeds 1 GB compressed |
| ArgsEscaped | Warns if deprecated `ArgsEscaped` is set |

**Full mode** (default, downloads all layers) adds:

| Check | Description |
|-------|-------------|
| Config digest | Config blob SHA256 matches manifest descriptor |
| Config size | Config blob size matches manifest descriptor |
| Diff IDs | Decompressed layer SHA256 matches config diff_ids |
| Tar validity | Each layer is a valid tar archive |
| Whiteout files | `.wh.*` entries are well-formed |
| Tar headers | No negative timestamps or dangerous permissions |
| Duplicate files | Warns about files appearing in multiple layers |

**Examples:**

```bash
# Full check from registry
occystrap check registry://docker.io/library/busybox:latest

# Fast metadata-only check
occystrap check --fast registry://docker.io/library/busybox:latest

# JSON output for CI integration
occystrap -O json check registry://docker.io/library/busybox:latest

# Check a tarball
occystrap check tar://image.tar

# Check local Docker image
occystrap check docker://myimage:v1
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
| `max_workers=N` | Number of parallel download threads (default: 4) |

Layers are downloaded in parallel using a thread pool for improved performance.

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

### dockerpush://

Fetch images from the local Docker or Podman daemon using an embedded registry.
This is faster than `docker://` for multi-layer images because Docker pushes
layers in parallel using the Registry V2 protocol, rather than exporting the
entire image as a single sequential tarball.

```
dockerpush://IMAGE:TAG[?socket=/path/to/socket]
```

**Query Options:**

| Option | Description |
|--------|-------------|
| `socket=/path` | Custom daemon socket path |

**Examples:**

```bash
# Docker daemon (default socket)
dockerpush://myimage:v1

# Podman (rootful)
dockerpush://myimage:v1?socket=/run/podman/podman.sock

# Compare speed with docker:// for multi-layer images
dockerpush://python:3.11
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
registry://HOST/IMAGE:TAG[?insecure=true&compression=TYPE&max_workers=N]
```

**Query Options:**

| Option | Description |
|--------|-------------|
| `insecure=true` | Use HTTP instead of HTTPS |
| `compression=TYPE` | Layer compression: gzip (default) or zstd |
| `max_workers=N` | Number of parallel upload threads (default: 4) |

Layers are uploaded in parallel using a thread pool for improved performance.
The `max_workers` option controls the number of concurrent uploads.

**Examples:**

```bash
# Push to private registry
registry://myregistry.example.com/myproject/myimage:v1

# Push with insecure (HTTP)
registry://internal.local/image:tag?insecure=true

# Push with zstd compression (requires Docker 20.10+ or containerd 1.5+)
registry://myregistry.example.com/myimage:v1?compression=zstd

# Push with 8 parallel upload threads
registry://myregistry.example.com/myimage:v1?max_workers=8
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

## Layer Cache

The `--layer-cache` option enables a persistent cache that tracks which
layers have already been processed and uploaded to a registry. This is
particularly useful in CI environments where multiple images sharing
common base layers are pushed in sequence.

### How It Works

When pushing an image to a registry, occystrap normally fetches each
layer, applies any filters, compresses the result, and uploads it.
With `--layer-cache`, occystrap records the mapping from each input
layer to its compressed output after a successful upload. On subsequent
runs, if a layer's input DiffID is found in the cache (with matching
filter configuration), occystrap verifies the compressed blob still
exists in the target registry via a HEAD request. If it does, the
entire layer is skipped -- no fetch, no filter, no compress, no upload.

### Usage

```bash
# First push: all layers processed normally, results cached
occystrap --layer-cache /tmp/cache.json \
    process docker://app1:v1 registry://myregistry/app1:v1

# Second push: shared base layers are skipped
occystrap --layer-cache /tmp/cache.json \
    process docker://app2:v1 registry://myregistry/app2:v1
```

The cache path can also be set via the `OCCYSTRAP_LAYER_CACHE`
environment variable.

### `dockerpush://` Integration

When using `dockerpush://` as the input source with `--layer-cache`, occystrap
enables a HEAD optimization that skips cached layers *before Docker even
uploads them*. On the first run, all layers are transferred normally. On
subsequent runs, the embedded registry returns `200` for HEAD checks on cached
layers, causing Docker to skip the upload entirely. This means cached layers
have zero local transfer overhead.

A digest mapping file (`{cache_path}.digests`) is created alongside the
cache to translate between Docker's compressed digests and the DiffIDs used
as cache keys.

```bash
# First push: all layers transferred and cached
occystrap --layer-cache /tmp/cache.json \
    process dockerpush://app:v1 registry://myregistry/app:v1

# Second push: Docker skips uploading shared layers entirely
occystrap --layer-cache /tmp/cache.json \
    process dockerpush://app:v2 registry://myregistry/app:v2
```

### Pipeline Awareness

Cache entries are keyed by the input layer DiffID and a hash of the
active pipeline configuration (filter chain **and** compression type).
This means layers processed with different filter configurations
(e.g., with vs. without `normalize-timestamps`) or different
compression formats (gzip vs. zstd) get separate cache entries and
will not incorrectly reuse results from a different pipeline.

### Cache Growth

The cache currently has no automatic eviction or size limit. For
typical container image workloads the file stays small (one entry per
unique layer), but long-lived caches spanning many unrelated images
may grow over time. You can safely delete the cache file at any point
to start fresh -- the only cost is re-processing layers on the next
push.

### Cache File Format

The cache is stored as a JSON file:

```json
{
  "version": 1,
  "layers": {
    "sha256:abc123...": {
      "compressed_digest": "sha256:def456...",
      "compressed_size": 45678901,
      "media_type": "application/vnd.docker.image.rootfs.diff.tar.gzip",
      "filters_hash": "none",
      "timestamp": "2026-02-09T12:34:56.789012+00:00"
    }
  }
}
```

| Field | Description |
|-------|-------------|
| `version` | Cache format version (currently 1) |
| `layers` | Map from input DiffID to cached metadata |
| `compressed_digest` | SHA256 digest of the compressed blob |
| `compressed_size` | Size of the compressed blob in bytes |
| `media_type` | OCI/Docker media type of the compressed layer |
| `filters_hash` | SHA256 of the pipeline config (filters + compression), or `"none"` |
| `timestamp` | ISO 8601 timestamp of when the entry was recorded |

The cache file is written atomically (via temporary file and rename)
to prevent corruption if the process is interrupted.

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
