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

Container images consist of two types of elements, represented as
`ImageElement` dataclass instances:

```python
@dataclasses.dataclass
class ImageElement:
    element_type: str   # CONFIG_FILE or IMAGE_LAYER
    name: str           # Filename or digest hash
    data: object        # File-like object or None (skipped)
    layer_index: int | None = None  # Manifest position
```

| Element Type | Description |
|--------------|-------------|
| `CONFIG_FILE` | JSON file containing image metadata and configuration |
| `IMAGE_LAYER` | Tarball containing a filesystem layer |

Each element flows through the pipeline independently, allowing streaming
processing without loading entire images into memory. The `layer_index`
field is set when layers are delivered out of order (see
[Out-of-Order Delivery](#out-of-order-layer-delivery) below).

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
    └── If ordered=True: yield layers in manifest order
    └── If ordered=False: yield layers as downloads complete
        (each with layer_index for reordering)
```

Key aspects:
- All layers download simultaneously to maximize throughput
- When `ordered=True`, layers are yielded in manifest order
- When `ordered=False`, layers are yielded via `as_completed()` with
  `layer_index` set, eliminating unnecessary waiting
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

### Docker Push Input

Fetches images from local Docker or Podman daemons using an embedded registry.

```
dockerpush://IMAGE:TAG
```

**Why use `dockerpush://` instead of `docker://`?**

The Docker Engine API (`/images/{name}/get`) exports images as a single
sequential tarball. This is fundamentally slow for multi-layer images because
it serializes all layers into one stream, with no opportunity for
parallelization. The entire tarball must be read before the manifest becomes
available.

Docker's own `push` command, however, uses the Registry V2 HTTP API, which
transfers layers individually and in parallel. The `dockerpush://` input
exploits this by starting a minimal HTTPS server on localhost (with an
ephemeral self-signed certificate) that implements the V2 push-path
endpoints. Docker pushes layers to this server just as it would push to
any registry, but the received data feeds directly into the occystrap
pipeline.

Docker treats the `127.0.0.0/8` range as insecure (skipping certificate
verification), so the self-signed certificate is accepted without any
daemon.json changes. The server uses HTTPS rather than plain HTTP because
some Docker versions do not fall back from HTTPS to HTTP for loopback
addresses.

**How it works:**

```
1. Generate ephemeral self-signed TLS certificate (via openssl)
2. Start ThreadingHTTPServer on 127.0.0.1 (ephemeral port, TLS-wrapped)
3. Tag image for localhost push (POST /images/{name}/tag)
4. Push image (POST /images/{name}/push)
   - Docker uploads layers in parallel to embedded registry
   - Server thread handles uploads, stores blobs as temp files
5. Wait for manifest from Docker push
6. Parse manifest + config to get layer DiffIDs
7. Yield config element
8. For each layer: read blob, decompress, yield ImageElement
9. Cleanup: untag temp tag, stop server, delete temp files
```

**Threading model:**

The embedded registry runs in a daemon thread handling Docker's parallel
uploads. The main thread waits for the manifest to arrive, then reads the
received blobs and yields pipeline elements. Shared state between threads
is protected by a threading lock.

**Layer cache integration:**

When `--layer-cache` is used with a `registry://` output and a `dockerpush://`
input, the embedded registry uses a HEAD optimization to skip cached layers
*before Docker even uploads them*. On the first run, Docker uploads all layers
normally and occystrap records a mapping between Docker's compressed digests
and the uncompressed DiffIDs. On subsequent runs, the embedded registry returns
`200` for HEAD checks on cached layers, causing Docker to skip the upload
entirely. This means cached layers consume zero local transfer time.

The digest mapping is stored alongside the layer cache as
`{cache_path}.digests`. This file maps Docker's compressed layer digests to
the uncompressed DiffIDs used as cache keys. It is updated automatically
after each push.

**Limitations:**

- Only supports single-platform V2 manifests. If Docker pushes a manifest
  list (fat manifest) for a multi-arch image, parsing will fail. Use the
  `registry://` input for multi-arch images.
- The manifest wait timeout defaults to 300 seconds (`MANIFEST_TIMEOUT`
  constant in `dockerpush.py`). Very large images on slow systems may
  need this value increased.

**When to use:**

- Use `dockerpush://` when the source image is in a local Docker daemon
  and the image has multiple layers. The speed advantage grows with the
  number and size of layers.
- Use `docker://` for single-layer images or when minimal overhead is
  preferred (the embedded registry adds a small amount of setup time).
- Use `dockerpush://` with `--layer-cache` for maximum performance in CI
  workflows pushing multiple images that share base layers.

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

    def process_image_element(self, element):
        # Transform the element
        modified_data = transform(element.data)
        modified_name = new_name_if_changed

        # Pass to wrapped output with a new ImageElement
        self.wrapped.process_image_element(
            constants.ImageElement(
                element.element_type, modified_name,
                modified_data,
                layer_index=element.layer_index))
```

Filters propagate the `requires_ordered_layers` property from their
wrapped output, so the pipeline respects the final output's ordering
needs.

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

All output writers log a summary line at the end of processing.

The registry output provides a detailed breakdown of where time was spent:

```
Processed 40 layers in 34.7s (compress: 15.8s, upload: 4.5s,
  upload_skipped: 22), 980.0 MB in, 326.3 MB out (33%)
```

This shows:
- **compress** - total CPU time spent compressing layers (summed across threads)
- **upload** - total time spent on upload HTTP requests (summed across threads)
- **upload_skipped** - number of blobs that already existed in the registry
- **MB in / MB out** - uncompressed input size vs compressed output size
- **ratio** - compression ratio (compressed / uncompressed as percentage)

Other outputs log a simpler summary:

```
Processed 12345678 bytes in 5 layers in 3.2 seconds
```

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
- Layer order is preserved by tracking `layer_index` and sorting at
  finalize time
- Authentication token updates are thread-safe
- Progress is reported every 10 seconds during finalize
- Default parallelism is 4 threads, configurable via `--parallel` or `-j`,
  or the `max_workers` URI option

**Cross-Invocation Layer Cache:**

When pushing multiple images that share base layers (common in CI), the
`--layer-cache` option enables persistent caching of layer processing results:

```
fetch_callback(digest)
    └── Check cache for (digest, filters_hash)
    └── If found: HEAD request to verify registry still has blob
    └── If registry has blob: skip layer (no fetch/filter/compress/upload)
    └── If not: process normally and record result to cache
```

Cache entries are keyed by `(input_diffid, filters_hash)` so that the same
layer processed with different filter configurations gets separate entries.
The cache is stored as a JSON file with one entry per layer, recording
the compressed digest, size, media type, and filter hash. The cache is
saved atomically to disk (via temporary file and rename) after each
successful push. Cache hits are reported in the summary line.

See [Command Reference](/components/occystrap/command-reference/#layer-cache) for the full
cache file format and usage examples.

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

### Out-of-Order Layer Delivery

The pipeline supports out-of-order layer delivery to maximize throughput
when the output doesn't require manifest ordering. Each output declares
its ordering needs via the `requires_ordered_layers` property:

- **Order-dependent** (`True`): `dir` with `expand=True`, `oci`
- **Order-independent** (`False`): `registry`, `tar`, `docker`,
  `dir` (expand=False), `mounts`

When `requires_ordered_layers` is `False`:
1. The input's `fetch()` receives `ordered=False`
2. Layers are yielded as they become available with `layer_index` set
3. The output stores layers with their indices
4. `finalize()` sorts by index to reconstruct the correct manifest order

This is particularly beneficial for the registry-to-registry pipeline,
where layers can start uploading as soon as they finish downloading
rather than waiting for earlier layers to complete first.

### Hash Recalculation

When filters modify layer content (timestamps, file exclusion), the SHA256
hash changes. Filters that modify content:

1. Process the layer tarball
2. Calculate the new SHA256 hash
3. Update the layer name to use the new hash
4. Update the manifest to reference the new hash
