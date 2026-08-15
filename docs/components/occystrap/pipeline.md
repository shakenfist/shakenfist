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
    temp_path: str | None = None    # Backing temp file, if any
```

| Element Type | Description |
|--------------|-------------|
| `CONFIG_FILE` | JSON file containing image metadata and configuration |
| `IMAGE_LAYER` | Tarball containing a filesystem layer |

Each element flows through the pipeline independently, allowing streaming
processing without loading entire images into memory. The `layer_index`
field is set when layers are delivered out of order (see
[Out-of-Order Delivery](#out-of-order-layer-delivery) below).

`temp_path` names the temp file backing `data`, when the input had to spill
the layer to disk. An output writer may `os.rename()` that file into place
instead of copying bytes out of the file handle, which on the same
filesystem avoids the copy entirely.

**The hand-off rule:** an input only sets `temp_path` when it is willing to
give the file away — `inputs/docker.py` passes it only for the last
reference to a buffered layer, since an earlier reference still needs the
file for a later yield. The input then unlinks the path in a `finally`
block guarded by `os.path.exists()`, so an output that renamed the file
away turns that cleanup into a no-op rather than an error.

Two rules follow for anyone writing either half. An output must not move a
file that has no `temp_path`, and must not touch `temp_path` after
`process_image_element()` returns — the input frees it as soon as the call
unwinds. An input must not set `temp_path` on a file it still needs, and
must keep its unlink existence-guarded rather than unconditional.
`outputs/directory.py` is the reference implementation of the output half,
including the `shutil.move()` fallback for a cross-filesystem rename.

## Input Sources

Input sources implement the `ImageInput` interface (`inputs/base.py`) and
provide image elements from various sources.

```python
class ImageInput(ABC):
    image: str    # abstract property, the image name
    tag: str      # abstract property, the image tag

    @abstractmethod
    def fetch(self, fetch_callback=None, ordered=True):
        """Yield ImageElement instances."""

    def get_manifest(self): return None
    def get_config(self): return None
```

`fetch()` is the only abstract method. `fetch_callback` takes a layer
digest and returns False to skip that layer, in which case the element is
still yielded but with `data=None`. `ordered=True` yields layers in
manifest order; `ordered=False` yields them as they become available with
`layer_index` set (see
[Out-of-Order Delivery](#out-of-order-layer-delivery)).

`get_manifest()` and `get_config()` are optional metadata accessors that
must not download layer blobs. They are what the read-only `info` and
`check` commands consume. Both default to returning `None`, and **`None`
means "this source cannot answer", not "the image has no such data"** — so
a caller has to handle the null case rather than assume it is a failure:

| Input | `get_manifest()` | `get_config()` |
|-------|------------------|----------------|
| `registry://`, `quay://` | Yes | Yes |
| `docker://` | `None` — the daemon exposes no distribution manifest | Yes |
| `tar://` | `None` | Yes |
| `dockerpush://` | `None` | `None` — nothing is known until Docker pushes |

`_build_info()` in `main.py` is the reference for degrading gracefully
across all four cases.

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

To minimize disk usage for large images, the Docker input streams the
tarball sequentially and only buffers to a temp file when a layer arrives
out of order. In the optimistic case no temp files are used at all, so a
26GB image with in-order layers costs near zero disk. Temp file location
is configurable via `--temp-dir`.

The mechanism — the inspect API call that pre-computes the manifest,
tarball format detection, and the zero-buffering path for Docker 25+ — is
described in
[internals.md](/components/occystrap/internals/#docker-daemon-hybrid-streaming).

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

### The diff_id contract

A filter that changes layer content invalidates the `rootfs.diff_ids` list
in the image config, and the config normally arrives *before* the layers it
describes. `ImageFilter` in `filters/base.py` resolves that ordering
problem with four helpers, and a content-modifying filter must use all of
them rather than forwarding the config itself:

| Helper | Purpose |
|--------|---------|
| `_buffer_config(element)` | Hold the `CONFIG_FILE` back instead of forwarding it |
| `_record_new_diff_id(sha256_hex, layer_index, original_hex=None)` | Record the digest of a rewritten layer |
| `_skip_layer(layer_index)` | Advance the layer counter for a layer left untouched |
| `_forward_buffered_config()` | Rewrite `diff_ids` and forward the config |

`finalize()` calls `_forward_buffered_config()` for you, so the config is
emitted last with the corrected digests. `_skip_layer()` matters because
under ordered delivery `layer_index` is `None` and the base class is
counting positions itself — a filter that silently drops a layer without
calling it leaves every later diff_id attributed to the wrong position.

`original_hex` is what makes the mapping usable across images. When it is
supplied and differs from the new digest, the `original -> filtered` pair
is recorded in the shared `diff_id_map`. Content-modifying filters must
therefore accept a `diff_id_map` kwarg and forward it to the base class,
and `PipelineBuilder.build_filter()` must be taught to pass it. Proxy mode
depends on this: a layer already rewritten for one image is recognised when
a second image references the original digest.

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

Output writers implement the `ImageOutput` interface (`outputs/base.py`)
and handle the final destination of processed elements.

```python
class ImageOutput(ABC):
    @property
    def requires_ordered_layers(self): ...

    @abstractmethod
    def fetch_callback(self, digest): ...

    @abstractmethod
    def process_image_element(self, element): ...

    @abstractmethod
    def finalize(self): ...

    def verify(self, full=False): return CheckResults()
```

`fetch_callback()` returns False for a layer the destination already has,
which is how a writer avoids paying for a download it would discard.
`requires_ordered_layers` tells the driver whether this writer can cope
with out-of-order delivery; the driver passes it straight to `fetch()` as
`ordered`. `finalize()` runs once after the last element and is where
manifests get written and files closed.

`verify()` is optional and runs *after* `finalize()` and after any
post-processing step such as `write_bundle()`. The base implementation
returns an empty, passing `CheckResults`. `full=False` is the fast path —
check that blobs and files exist and are the right size; `full=True`
re-reads and revalidates the data. Record what you expect during
`process_image_element()` and assert it in `verify()`; `DirWriter` is the
reference implementation. `RegistryWriter.verify()` is the case worth
knowing about, because `finalize()` closes every HTTP client, so it has to
build a fresh one rather than reusing the pooled clients.

### Driving the pipeline

`_fetch()` in `main.py` is the driver that connects the two halves:

```python
ordered = output.requires_ordered_layers
with redirect_logging():
    for element in img.fetch(
            fetch_callback=output.fetch_callback,
            ordered=ordered):
        output.process_image_element(element)
    output.finalize()
```

The `redirect_logging()` context manager comes from `progress.py` and
routes log records around any active progress bar, so log lines do not
interleave with the bar's redraws. `_fetch()` also attaches a
`util.RequestStats` to the input if it has a `stats` attribute, and
returns the byte, layer, retry and rate-limit counters for the caller to
report.

All output writers log a structured summary line at the end of processing,
emitted with `LOG.with_fields()` so the fields survive into log
aggregation rather than being baked into a sentence.

`ImageOutput._log_summary()` in `outputs/base.py` produces the common
form, logged as `Processing complete`:

| Field | Meaning |
|-------|---------|
| `bytes` | Total bytes seen by the writer |
| `layers` | Number of `IMAGE_LAYER` elements written |
| `elapsed_s` | Wall clock seconds, to one decimal place |

The registry output overrides this in `RegistryWriter.finalize()` with a
detailed breakdown of where time was spent, logged as `Push complete`:

| Field | Meaning |
|-------|---------|
| `layers` | Number of layers in the pushed manifest |
| `elapsed_s` | Wall clock seconds for the whole push |
| `compress_s` | CPU time spent compressing layers, summed across threads |
| `upload_s` | Time spent on upload HTTP requests, summed across threads |
| `upload_skipped` | Blobs that already existed in the registry |
| `cache_hits` | Layers served from the cross-invocation layer cache |
| `input_mb` | Uncompressed input size |
| `output_mb` | Compressed output size |
| `ratio_pct` | Compression ratio, output over input, as a percentage |

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

The `compression.py` module that implements format detection, streaming
compression and decompression, and media type mapping is described in
[internals.md](/components/occystrap/internals/#layer-compression).

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

Per-output ordering requirements and the reasons behind them are
tabulated in
[internals.md](/components/occystrap/internals/#out-of-order-layer-delivery).

### Pipeline Reuse in Proxy Mode

The `proxy` command demonstrates that the pipeline is fully reusable.
Each received image gets a fresh pipeline built by `PipelineBuilder`,
and multiple images can be processed concurrently:

```
Proxy receives image push
    └── _handle_manifest_put() blocks HTTP response
    └── Increment blob refcounts (protects shared blobs)
    └── Acquire processing semaphore (backpressure)
    └── Create _ProxyInput (synthetic ImageInput from received blobs)
    └── PipelineBuilder builds fresh output + filters
    └── Run pipeline: fetch() → filters → RegistryWriter
    └── Decrement refcounts, delete blobs at refcount 0
    └── Return 201/500 to client
```

The proxy's own internals — pull-through caching, per-image locking and
the filtering rules — are in
[internals.md](/components/occystrap/internals/#filtering-registry-proxy).

`PipelineBuilder.build_pipeline()` creates new input/output/filter
instances on each call with no shared mutable state, so running the
pipeline multiple times in one process is safe. The proxy keeps a
single `LayerCache` (internally thread-safe) across images for
cross-image layer dedup. Blob reference counting ensures shared
blobs are not deleted while another concurrent manifest still
references them.

### Hash Recalculation

When filters modify layer content (timestamps, file exclusion), the SHA256
hash changes. Filters that modify content:

1. Process the layer tarball
2. Calculate the new SHA256 hash
3. Update the layer name to use the new hash
4. Update the manifest to reference the new hash

## Security Sanitization

Occystrap includes two security helpers in `occystrap/util.py`
for preventing common injection attacks when handling
user-controlled data from HTTP requests and file paths.

### HTTP Response Splitting (CWE-113)

HTTP handler classes (`EmbeddedRegistryHandler`,
`ProxyRegistryHandler`) inherit from `SafeHeaderMixin`,
which overrides `send_header()` to strip `\r` and `\n`
from header values. Additionally, `sanitize_header_value()`
is called at each call site where user-controlled data
flows into headers, satisfying CodeQL's taint analysis.

### Path Traversal (CWE-22)

Output writers that construct file paths from image names,
tags, digests, or layer paths use `safe_path_join()` instead
of bare `os.path.join()`. This resolves the joined path via
`os.path.realpath()` and validates it stays within the
intended base directory, raising `PathEscapeError` if
traversal is detected.

See `PLAN-header-safety.md` in the project root for full
design rationale.
