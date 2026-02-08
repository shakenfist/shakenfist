# Docker Tarball Format Reference

This document describes the various `docker save` tarball formats that have
existed over Docker's history, and which formats occystrap supports.

## Format Summary

| Era | Docker Versions | Key Files | occystrap Support |
|-----|-----------------|-----------|-------------------|
| Legacy | pre-1.10 (~2013-2016) | `repositories` | **No** |
| Content-Addressable | 1.10 - 24.x (2016-2023) | `manifest.json` | **Yes** |
| OCI-Compatible | 25.0+ (2024-present) | `manifest.json` + `index.json` | **Yes** |

## Legacy Format (pre-1.10)

**Not supported by occystrap.**

Docker's original format used randomly-assigned UUIDs for layer identification
and did not include a `manifest.json` file.

### Structure

```
image.tar
├── repositories                      # Image name/tag -> top layer UUID
├── <uuid-1>/
│   ├── VERSION                       # Always "1.0"
│   ├── json                          # Layer metadata with "parent" field
│   └── layer.tar                     # Layer filesystem
├── <uuid-2>/
│   ├── VERSION
│   ├── json                          # References uuid-1 as parent
│   └── layer.tar
└── ...
```

### Key Characteristics

- **No manifest.json** - the `repositories` file is the entry point
- **UUID-based naming** - layer directories use random 64-char hex IDs
- **Parent chain** - layers reference their parent via `"parent"` in json
- **Not content-addressed** - same content can have different IDs

### Why Not Supported

This format is from 2016 and earlier. Anyone with tarballs this old can convert
them to modern format:

```bash
# Load into modern Docker, then re-save
docker load < ancient-image.tar
docker save myimage:tag > modern-image.tar
```

## Content-Addressable Format (1.10 - 24.x)

**Fully supported by occystrap.**

Docker 1.10 (February 2016) introduced content-addressable storage, where layer
and image IDs are derived from SHA256 hashes of their content.

### Structure

```
image.tar
├── manifest.json                     # Main entry point
├── repositories                      # Legacy compatibility (optional)
├── <config-sha256>.json              # Image configuration
├── <layer1-sha256>/
│   └── layer.tar                     # Layer filesystem (uncompressed)
├── <layer2-sha256>/
│   └── layer.tar
└── ...
```

### manifest.json Schema

```json
[
  {
    "Config": "sha256abc123.json",
    "RepoTags": ["myimage:latest"],
    "Layers": [
      "sha256layer1/layer.tar",
      "sha256layer2/layer.tar"
    ]
  }
]
```

| Field | Type | Description |
|-------|------|-------------|
| `Config` | string | Filename of the image config JSON |
| `RepoTags` | []string | Image references (name:tag format) |
| `Layers` | []string | Ordered layer paths (base to top) |

### Key Characteristics

- **manifest.json** is the entry point
- **SHA256 digests** for content-addressed identification
- **Direct layer list** - no parent chain needed
- **Layers are uncompressed** tar archives

## OCI-Compatible Format (25.0+)

**Fully supported by occystrap.**

Docker 25 (January 2024) added OCI Image Layout support while maintaining
backwards compatibility with the content-addressable format.

### Structure

```
image.tar
├── index.json                        # OCI entry point
├── oci-layout                        # OCI version marker
├── manifest.json                     # Legacy compatibility
├── repositories                      # Legacy compatibility
├── blobs/
│   └── sha256/
│       ├── <config-digest>           # Image config blob
│       ├── <manifest-digest>         # OCI manifest
│       └── <layer-digest>            # Layer blobs (may be compressed)
└── ...
```

### Dual Format

Docker 25+ tarballs contain **both** OCI layout and legacy format:

- **OCI tools** use `index.json` and `blobs/sha256/`
- **Legacy tools** use `manifest.json` (occystrap uses this path)

### manifest.json in OCI Format

```json
[
  {
    "Config": "blobs/sha256/abc123...",
    "RepoTags": ["myimage:latest"],
    "Layers": [
      "blobs/sha256/def456...",
      "blobs/sha256/ghi789..."
    ]
  }
]
```

Note: Layer paths point to `blobs/sha256/<digest>` instead of `<digest>/layer.tar`.

### Key Characteristics

- **index.json** is the OCI entry point (occystrap uses manifest.json)
- **blobs/ directory** contains all content-addressed blobs
- **Layers may be compressed** (gzip or zstd)
- **Multi-architecture support** via manifest lists

## Entry Ordering in the Tarball Stream

A critical detail for streaming consumers: **manifest.json is always near the
end** of the tarball. This is not specified by any standard -- it is an
implementation detail that differs between Docker and Podman, but the result
is the same.

### Docker (Moby)

Docker writes all tarball files to a temporary directory on disk, then creates
the tarball using Go's `filepath.WalkDir()`, which walks files in **lexical
(alphabetical) order**. This means:

**Content-Addressable format (1.10-24.x):**

```
<config-hex>.json         # hex digits (0-9, a-f) sort first
<v1-compat-id>/json       # layer directories (also hex)
<v1-compat-id>/layer.tar
<v1-compat-id>/VERSION
manifest.json             # 'm' sorts after 0-9 and a-f
repositories              # 'r' sorts after 'm'
```

**OCI format (25+):**

```
blobs/sha256/<digest>     # all blobs (config, layers, OCI manifests)
index.json                # 'i' sorts after 'blobs/'
manifest.json             # 'm' sorts after 'i'
oci-layout                # 'o' sorts after 'm'
repositories              # 'r' sorts after 'o'
```

In both cases, all layer data and config arrive **before** manifest.json.

Source: [`moby/go-archive/archive.go`](https://github.com/moby/go-archive/blob/main/archive.go)
uses `filepath.WalkDir` at line ~693.

### Podman (containers/image)

Podman writes entries **directly to the tar stream** (no temp directory). Layer
blobs and config are written during the image copy operation. `manifest.json`
and `repositories` are written explicitly in `Close()`, making them the last
two entries.

Source: [`containers/image/docker/internal/tarfile/writer.go`](https://github.com/containers/image/blob/main/docker/internal/tarfile/writer.go)

### Implications

- **You cannot rely on manifest.json being in any specific position**
- Docker's own `docker load` extracts the entire tarball to a temp directory
  before reading manifest.json
- Streaming consumers must either buffer data before manifest.json arrives,
  or use an alternative source of manifest information

## The Docker Engine Inspect API

The Docker Engine API provides `GET /images/{name}/json` (the "inspect"
endpoint), which returns image metadata without streaming the full tarball.
This data can be used to pre-compute the manifest.

### Key Fields

| Field | Content | Example |
|-------|---------|---------|
| `Id` | `sha256:<config-hex>` | `sha256:abc123def456...` |
| `RootFS.Layers` | Array of DiffIDs | `["sha256:aaa...", "sha256:bbb..."]` |

- **Id** is the SHA256 hash of the image configuration JSON. This directly
  corresponds to the config filename in the tarball:
  - Content-Addressable: `<config-hex>.json`
  - OCI: `blobs/sha256/<config-hex>`

- **RootFS.Layers** contains DiffIDs (SHA256 hashes of the uncompressed layer
  tarballs). For Docker 25+ OCI format, these directly correspond to the blob
  paths: `blobs/sha256/<diffid-hex>`.

### Layer Identification: DiffIDs vs ChainIDs vs v1-Compat IDs

There are three types of layer identifiers in the Docker ecosystem:

| Type | Used For | Predictable from Inspect? |
|------|----------|--------------------------|
| **DiffID** | OCI blob paths (Docker 25+) | Yes (`RootFS.Layers`) |
| **ChainID** | Internal layer store | Computable but not used in tarballs |
| **v1-Compat ID** | Legacy layer dirs (1.10-24.x) | No (requires internal state) |

**DiffIDs** are the SHA256 of the uncompressed layer content. The inspect
API's `RootFS.Layers` field contains these.

**ChainIDs** are computed recursively:
`chain[0] = diff[0]`, `chain[n] = sha256(chain[n-1] + " " + diff[n])`.
These are used internally by Docker's graph driver, **not** in tarball paths.

**v1-Compat IDs** are deterministic IDs generated by `v1.CreateID()` in
Docker's codebase. They require internal Docker state and cannot be predicted
from the inspect API.

### Podman Compatibility

Podman provides a Docker-compatible API endpoint at the same path
(`GET /images/{name}/json`). The `Id` and `RootFS.Layers` fields are
populated identically. Occystrap's Docker input works with Podman by
pointing the socket to `/run/podman/podman.sock` (rootful) or
`/run/user/<uid>/podman/podman.sock` (rootless).

## Pre-Computed Manifest Optimization

Occystrap uses the inspect API to avoid blocking on manifest.json:

### Docker 25+ (OCI Format)

Since DiffIDs from inspect directly correspond to OCI blob paths, we can
pre-compute the full manifest before streaming begins:

```python
config_filename = 'blobs/sha256/<config-hex>'
expected_layers = ['blobs/sha256/<diffid>' for each DiffID]
```

This allows processing all blobs immediately as they arrive in the stream,
with no buffering required for in-order entries.

### Docker 1.10-24.x (Content-Addressable)

We can identify the config file early (`<config-hex>.json`), but cannot
predict layer directory names (v1-compat IDs). The config is yielded
immediately when seen, while layers are still buffered until manifest.json
arrives for ordering.

### Duplicate Layer Paths

A manifest can reference the same blob path multiple times. This happens
when multiple Dockerfile instructions (e.g., `ENV`, `CMD`, `EXPOSE`)
produce empty layers that share the same DiffID
(`5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef`,
the SHA256 of 1024 null bytes -- a minimal empty tar archive).

The blob exists only once in the tarball, but the manifest lists it at
multiple positions. Occystrap tracks reference counts for each blob path
and keeps the temp file alive until the last reference is consumed.

### Fallback Behavior

When inspect data is unavailable or incomplete (e.g., old API version,
missing `RootFS`), occystrap falls back to the original behavior of
buffering everything until manifest.json arrives.

When the pre-computed manifest differs from the actual manifest.json
(which should not happen in practice), occystrap logs a warning and
falls back to the actual manifest data.

## Format Detection

To identify which format a tarball uses:

```python
import tarfile

def identify_format(tarball_path):
    with tarfile.open(tarball_path) as tf:
        names = tf.getnames()

        if 'index.json' in names and 'oci-layout' in names:
            return 'oci-compatible'  # Docker 25+
        elif 'manifest.json' in names:
            return 'content-addressable'  # Docker 1.10-24
        elif 'repositories' in names:
            return 'legacy'  # Pre-1.10
        else:
            return 'unknown'
```

Occystrap's Docker daemon input detects format during streaming by checking
whether the first tarball entry starts with `blobs/` (OCI) or not (legacy).

## References

- [Docker Image Specification v1.2](https://github.com/moby/docker-image-spec/blob/v1.2.0/v1.2.md)
- [Docker Image Specification v1.3](https://github.com/moby/docker-image-spec/blob/main/spec.md)
- [OCI Image Layout Specification](https://github.com/opencontainers/image-spec/blob/main/image-layout.md)
- [Engine v1.10.0 Content Addressability Migration](https://github.com/moby/moby/wiki/Engine-v1.10.0-content-addressability-migration)
- [moby/moby tarexport source](https://github.com/moby/moby/tree/master/daemon/internal/image/tarexport)
- [Docker Engine API - Image Inspect](https://docs.docker.com/engine/api/v1.43/#tag/Image/operation/ImageInspect)
- [OCI Image Spec - Layer DiffID](https://github.com/opencontainers/image-spec/blob/main/config.md#layer-diffid)
- [OCI Image Spec - Layer ChainID](https://github.com/opencontainers/image-spec/blob/main/config.md#layer-chainid)
- [Podman Docker-compatible API](https://docs.podman.io/en/latest/markdown/podman-system-service.1.html)
