# Pipeline Architecture

Occy Strap processes container images using a flexible pipeline pattern. This
document explains how the pipeline works and how its components interact.

## Overview

The pipeline follows a simple flow:

```
Input Source  -->  Filter Chain (optional)  -->  Output Writer  -->  Files
```

1. **Input Source** reads image elements (config and layers) from a source
2. **Filters** transform or inspect elements as they pass through
3. **Output Writer** writes the processed elements to their destination

## Image Elements

Container images consist of two types of elements:

| Element Type | Description |
|--------------|-------------|
| `CONFIG_FILE` | JSON file containing image metadata and configuration |
| `IMAGE_LAYER` | Tarball containing a filesystem layer |

Each element flows through the pipeline independently, allowing streaming
processing without loading entire images into memory.

## Input Sources

Input sources implement the `ImageInput` interface and provide image elements
from various sources.

### Registry Input

Fetches images from Docker/OCI registries using the HTTP API.

```
registry://HOST/IMAGE:TAG
```

Capabilities:
- Token-based and basic authentication
- Multi-architecture image selection
- Manifest parsing (v1, v2, OCI formats)
- Individual layer blob fetching
- Parallel layer downloads for improved throughput

**Parallel Downloads:**

Layer blobs are downloaded in parallel using a thread pool:

```
fetch() generator
    └── Yield config file first (synchronous)
    └── Submit all layer downloads to thread pool
    └── Yield layers in order as downloads complete
```

Key aspects:
- All layers download simultaneously to maximize throughput
- Layers are yielded in order despite parallel download
- Authentication is thread-safe
- Default parallelism is 4 threads, configurable via `--parallel`

### Docker Daemon Input

Fetches images from local Docker or Podman daemons.

```
docker://IMAGE:TAG
```

Uses the Docker Engine API over Unix socket to stream the image tarball
(equivalent to `docker save`).

**Note:** The Docker Engine API only provides complete image export - there's
no way to fetch individual layers separately. This is a limitation of the API
design.

**Hybrid Streaming:**

To minimize disk usage for large images, the Docker input uses a hybrid
streaming approach:

```
fetch() generator
    └── Stream tarball sequentially (mode='r|')
    └── Read manifest.json to get expected layer order
    └── For each file in stream:
        ├── If next expected layer: yield directly (zero disk I/O)
        └── If out-of-order: buffer to temp file for later
    └── After stream: yield remaining buffered layers in order
```

Key aspects:
- In the optimistic case (layers in order), no temp files are used
- Out-of-order layers are buffered to individual temp files
- Temp files are deleted immediately after yielding
- For a 26GB image with in-order layers, disk usage is near zero
- Temp file location is configurable via `--temp-dir` option

### Tarball Input

Reads images from existing docker-save format tarballs.

```
tar:///path/to/file.tar
```

Parses `manifest.json` to locate config files and layers within the tarball.

## Filters

Filters implement the decorator pattern, wrapping outputs (or other filters)
to transform or inspect elements. They inherit from `ImageFilter`.

### How Filters Work

```python
# Conceptual filter structure
class MyFilter(ImageFilter):
    def __init__(self, wrapped_output):
        self.wrapped = wrapped_output

    def process_image_element(self, element_type, name, data):
        # Transform the element
        modified_data = transform(data)
        modified_name = new_name_if_changed

        # Pass to wrapped output
        self.wrapped.process_image_element(element_type, modified_name,
                                           modified_data)
```

### Filter Capabilities

Filters can:

- **Transform data** - Modify element content (e.g., normalize timestamps)
- **Transform names** - Rename elements (e.g., after hash changes)
- **Inspect elements** - Read without modification (e.g., search)
- **Skip elements** - Exclude elements from output
- **Accumulate state** - Track information across elements

### Available Filters

**normalize-timestamps**: Rewrites layer tarballs to set all file modification
times to a consistent value. Since this changes content, SHA256 hashes are
recalculated.

**search**: Searches layer contents for files matching patterns. Can operate
as search-only (prints results) or passthrough (searches AND forwards
elements).

**exclude**: Removes files matching glob patterns from layers, recalculating
hashes afterward.

**inspect**: Records layer metadata (digest, size, build history) to a JSONL
file. This is a pure passthrough filter -- it does not modify image data. Place
it between other filters to observe and measure their effect on layers.

### Chaining Filters

Multiple filters are chained together:

```bash
occystrap process registry://... tar://output.tar \
    -f normalize-timestamps \
    -f "search:pattern=*.conf" \
    -f "exclude:pattern=**/.git/**"
```

The pipeline becomes:

```
Input --> normalize-timestamps --> search --> exclude --> Output
```

Each filter wraps the next, forming a chain that processes elements in order.

## Output Writers

Output writers implement the `ImageOutput` interface and handle the final
destination of processed elements.

All output writers log a summary line at the end of processing:

```
Processed 12345678 bytes in 5 layers in 3.2 seconds
```

This shows the total bytes processed, layer count, and elapsed time.

### Tarball Output

Creates docker-loadable tarballs in v1.2 format.

```
tar:///path/to/output.tar
```

The tarball contains:
- `manifest.json` - Image manifest
- `<hash>.json` - Config file
- `<hash>/layer.tar` - Layer tarballs

Can be loaded with `docker load -i output.tar`.

### Directory Output

Extracts images to directories.

```
dir:///path/to/directory
```

Options:
- `unique_names=true` - Enable layer deduplication by prefixing filenames
- `expand=true` - Extract layer tarballs to filesystem

With `unique_names`, a `catalog.json` tracks which layers belong to which
images, allowing multiple images to share storage.

### OCI Bundle Output

Creates OCI runtime bundles for runc.

```
oci:///path/to/bundle
```

Produces:
- `config.json` - OCI runtime configuration
- `rootfs/` - Merged filesystem from all layers

### Registry Output

Pushes images to Docker/OCI registries.

```
registry://HOST/IMAGE:TAG
```

Uploads layers as blobs in parallel and creates the manifest.

**Parallel Compression and Uploads:**

Both layer compression and uploads run in a thread pool for improved performance:

```
process_image_element() called for each layer
    └── Read layer data
    └── Submit (compress + upload) to thread pool (non-blocking)
    └── Main thread continues to next layer

finalize()
    └── Wait for all compression/upload tasks to complete
    └── Collect layer metadata from futures (in order)
    └── Push manifest only after all blobs uploaded
```

Key design aspects:
- Multiple layers can compress simultaneously, utilizing multiple CPU cores
- While one layer is compressing, others can be uploading
- Layer order is preserved by collecting futures in submission order
- Authentication token updates are thread-safe
- Progress is reported every 10 seconds during finalize
- Default parallelism is 4 threads, configurable via `--parallel` or `-j`,
  or the `max_workers` URI option

**Blob Deduplication:**

Before uploading a layer blob, the registry output checks whether the blob
already exists in the target registry using `HEAD /v2/<name>/blobs/<digest>`.
If the blob exists, the upload is skipped. This is particularly effective when
pushing images that share base layers with images already in the registry.

For this check to work, the compressed blob must have the same SHA256 digest
as the existing blob. This requires deterministic compression -- see
[Deterministic Compression](#deterministic-compression) below.

### Docker Daemon Output

Loads images into local Docker or Podman.

```
docker://IMAGE:TAG
```

Uses the Docker Engine API to load the image.

## Data Flow Example

Consider this command:

```bash
occystrap process registry://docker.io/library/busybox:latest \
    tar://busybox.tar -f normalize-timestamps
```

The data flow is:

```
1. Registry Input fetches manifest from docker.io
2. Registry Input yields CONFIG_FILE element
   --> TimestampNormalizer passes through unchanged
   --> TarWriter writes to tarball
3. For each layer:
   a. Registry Input fetches layer blob
   b. Registry Input yields IMAGE_LAYER element
   c. TimestampNormalizer rewrites tarball with epoch timestamps
   d. TimestampNormalizer recalculates SHA256
   e. TimestampNormalizer yields modified element with new name
   f. TarWriter writes modified layer to tarball
4. TarWriter.finalize() writes manifest.json
```

## Key Concepts

### Whiteout Files

OCI layers use special files to mark deletions:

- `.wh.<filename>` - Marks a specific file as deleted
- `.wh..wh..opq` - Marks entire directory as opaque (replaced)

These are processed when extracting layers with `expand=true`.

### Layer Deduplication

With `unique_names=true`, layers are stored with content-addressed names.
When downloading multiple images:

1. First image stores layers normally
2. Subsequent images check if layers already exist
3. Shared layers are referenced, not duplicated
4. `catalog.json` maps images to their layers

### Deterministic Compression

When pushing layers to a registry, Occy Strap compresses them before upload.
For blob deduplication to work (skipping uploads of layers that already
exist), the compressed output must be identical for identical input. This
is called deterministic compression.

**gzip:** The gzip format includes a timestamp in its header by default,
which means compressing the same data twice produces different output.
Occy Strap suppresses this by setting `mtime=0` in the gzip header,
making gzip compression fully deterministic.

**zstd:** The zstd format does not embed timestamps, so it is inherently
deterministic. Compressing the same data with the same settings always
produces identical output.

This determinism works together with filters like `normalize-timestamps`
and `exclude` to maximize layer deduplication:

1. The `normalize-timestamps` filter sets all file modification times in
   layer tarballs to a consistent value (epoch 0 by default)
2. The `exclude` filter removes unwanted files from layers
3. Deterministic compression ensures the compressed output has a stable
   SHA256 digest
4. The registry output checks for existing blobs before uploading,
   skipping any that already exist

This means that if two images share identical layers (after filtering),
the second push will skip uploading those layers entirely.

### Hash Recalculation

When filters modify layer content (timestamps, file exclusion), the SHA256
hash changes. Filters that modify content:

1. Process the layer tarball
2. Calculate the new SHA256 hash
3. Update the layer name to use the new hash
4. Update the manifest to reference the new hash
