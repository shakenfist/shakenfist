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

## References

- [Docker Image Specification v1.2](https://github.com/moby/docker-image-spec/blob/v1.2.0/v1.2.md)
- [OCI Image Layout Specification](https://github.com/opencontainers/image-spec/blob/main/image-layout.md)
- [Engine v1.10.0 Content Addressability Migration](https://github.com/moby/moby/wiki/Engine-v1.10.0-content-addressability-migration)
- [moby/moby tarexport source](https://github.com/moby/moby/tree/master/daemon/internal/image/tarexport)
