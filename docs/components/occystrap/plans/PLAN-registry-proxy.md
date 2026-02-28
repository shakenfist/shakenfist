# Registry Proxy Mode (dockerpush as persistent registry)

## Prompt

Before responding to questions or discussion points in this
document, explore the occystrap codebase thoroughly. Read relevant
source files, understand existing patterns (project structure,
command-line argument handling, input source abstractions, output
formatting, error handling), and ground your answers in what the
code actually does today. Do not speculate about the codebase when
you could read it instead. Where a question touches on external
concepts (OCI image specs, Docker/Podman compatibility, registry
APIs), research as needed to give a confident answer. Flag any
uncertainty explicitly rather than guessing.

## Situation

The `dockerpush://` input provides fast parallel layer transfer from the
local Docker daemon, and filters allow transforming image contents before
pushing to a real registry. However, the current architecture requires
one occystrap invocation per image. The idea is to run occystrap as a
**persistent registry proxy**: Docker (or any V2 client) pushes images
to it, occystrap applies filters, and forwards the result to a real
downstream registry. This would allow processing multiple images through
a single long-running instance.

## Mission and problem statement

While `dockerpush://` is faster than the previous `tar://` and `docker://`
implementations of filtration, it is still not sufficiently performant
for the Kolla image build process I have been working on this tooling
for. A representative summary of the build process with timestamps would
be:

```
2026-02-24T21:51:10.9035078Z OCI registry health check
2026-02-24T21:51:13.3251419Z Download artifacts
2026-02-24T21:52:59.1839497Z Extract source and move to correct location
2026-02-24T21:53:01.5069043Z Build container images
2026-02-24T22:15:44.7926037Z Image build complete, push starts
2026-02-25T00:50:08.0620979Z Incomplete CI workflow terminated for having taken too long
```

It is hoped (but unproven) that a persistent proxy would be able to detect
duplicated layers better, and act as a shock absorber between image uploads.
If such a proxy was configured before build, then `kolla-build` could also
be configured to push as images were completed, and the build and upload
processes could therefore overlap.

## Current Constraints (Why This Doesn't Work Today)

The embedded registry in `dockerpush.py` has a single-image-per-session
design. Specifically:

### 1. `_RegistryState.manifest_data` is a scalar

```python
class _RegistryState:
    def __init__(self, temp_dir=None):
        self.manifest_data = None          # single manifest, not a list
        self.manifest_event = threading.Event()  # single event, set once
```

When `_handle_manifest_put()` fires, it overwrites `self.manifest_data`.
A second image push would silently clobber the first manifest.

### 2. Single `threading.Event` for manifest arrival

`fetch()` calls `manifest_event.wait()` exactly once, parses that one
manifest, yields its layers, then tears everything down. There is no
loop or queue to handle subsequent manifests.

### 3. Multi-arch manifests are explicitly rejected

```python
if 'manifests' in manifest:
    raise Exception(
        'Received a manifest list (multi-arch) instead of ...')
```

A full registry proxy would eventually need to handle manifest lists.
However, kolla-build produces single-platform images (it builds on the
host architecture), so this can be deferred -- rejecting manifest lists
initially is acceptable.

### 4. The `fetch()` flow is one-shot

The entire lifecycle is linear and non-repeatable:

```
tag one image -> start server -> push one image -> wait for one manifest
-> yield config + layers -> untag -> stop server -> cleanup temp files
```

The server is started and stopped within a single `fetch()` call. There
is no mechanism to keep the server running across multiple pushes.

### 5. Blob lifecycle is per-session

All blobs land in `state.blobs` keyed by digest hex. The flat namespace
is actually desirable: Docker content-addresses blobs, so two images
sharing a base layer push the same digest -- having one copy is correct
and gives us cross-image dedup for free. The real issue is lifecycle:
all blobs are cleaned up when the single `fetch()` call completes.
If processing image A deletes its blobs, and image B (still uploading)
shares some of them, those shared blobs are gone. With concurrent
pushes, blob deletion must be deferred until no pending manifest
references a given digest.

### 6. Docker API coupling

The current flow uses the Docker Engine API (`/images/{name}/tag`,
`/images/{name}/push`) to initiate pushes. In proxy mode, the client
(Docker, Podman, Buildkit, Skopeo, etc.) initiates the push itself --
occystrap just needs to be a listening registry, not an orchestrator.

## Key Design Insights

### The existing pipeline is already reusable

`PipelineBuilder.build_pipeline()` creates fresh input/output/filter
instances on each call. There is no global or module-level state that
would prevent running the pipeline multiple times in one process. The
core loop in `main.py` `_fetch()` (line ~100) is:

```python
for element in img.fetch(
        fetch_callback=output.fetch_callback, ...):
    output.process_image_element(element)
output.finalize()
```

The proxy's processing loop can call `build_pipeline()` per received
manifest, using a synthetic input that yields `ImageElement`s from the
already-received blobs. Each invocation gets fresh filters and output
state.

### LayerCache is the key to cross-image dedup

If the proxy keeps a single `LayerCache` instance across images,
`RegistryWriter.fetch_callback()` will find layers already pushed to
the downstream registry and skip them entirely -- no filtering, no
compression, no upload. For Kolla images sharing base layers, the first
image pays the full cost and all subsequent images skip those layers.
This is the "shock absorber" effect.

### Blocking manifest processing solves error propagation

Rather than returning 201 on the manifest PUT immediately and processing
asynchronously (which leaves the client unaware of downstream failures),
the handler should **block the HTTP response until downstream processing
completes**. This:

- Provides natural backpressure -- Docker won't start the next push
  until the current one is processed
- Lets kolla-build detect push failures via Docker's exit code
- Eliminates the need for a separate error-reporting channel
- Simplifies the design: no processing thread or queue needed for the
  initial implementation

Docker's push client is tolerant of slow registries (it already waits
for large blob uploads), so the timeout risk is manageable.

## Execution

### Phase 1: Persistent proxy (MVP)

**Goal**: occystrap runs as a long-lived process, receives pushes from
Docker, applies filters, and forwards to a downstream registry. Images
are processed sequentially (one at a time, blocking on manifest PUT).

#### New CLI subcommand

```bash
occystrap proxy \
    --listen 127.0.0.1:5050 \
    --downstream registry://registry.example.com \
    -f normalize-timestamps \
    -f exclude:/tmp
```

Usage:

```bash
# Start proxy before kolla-build
occystrap proxy --listen 127.0.0.1:5050 \
    --downstream registry://ghcr.io/myorg \
    --layer-cache /tmp/layer-cache.json \
    -f normalize-timestamps &

# Configure kolla-build to push to the proxy
kolla-build --registry 127.0.0.1:5050 --push ...
```

#### Decouple from Docker Engine API

In proxy mode, occystrap does **not** call the Docker Engine API at all.
It is purely a receiving registry. The client (Docker, Podman, Skopeo,
Buildkit) pushes to occystrap's address directly:

```bash
docker tag myimage:latest localhost:5050/myimage:latest
docker push localhost:5050/myimage:latest
```

The `_tag_image()`, `_push_image()`, and `_untag_image()` methods from
`dockerpush.py` are not used. The server just listens and processes
whatever arrives.

#### Repository name mapping

When Docker pushes to `localhost:5050/kolla/nova-api:latest`, the proxy
forwards to the downstream registry preserving the repository path:
`registry.example.com/kolla/nova-api:latest`. The repository name is
passed through as-is for the initial implementation. A `--prefix` flag
could be added later if rewriting is needed.

#### Manifest processing flow

`_handle_manifest_put()` blocks the HTTP response while processing:

1. Parse the manifest
2. Resolve blobs from `state.blobs` (Docker pushes blobs before
   the manifest, so they should already be present)
3. Build a fresh pipeline via `PipelineBuilder` using a synthetic
   input that yields `ImageElement`s from the received blobs
4. Run the filter chain and push to the downstream registry
5. On success: return 201 to the client
6. On failure: return 500 to the client (Docker will report the
   push as failed)
7. Clean up blobs referenced only by this manifest

#### Blob lifecycle (sequential mode)

With sequential processing (blocking manifest PUT), blob lifecycle is
simple: after processing a manifest, delete the blobs it referenced.
Shared layers between images are content-addressed, so if image B
pushes the same blob as image A, Docker's HEAD check will find it
already present (from image A's push) and skip the upload. The
`LayerCache` + `RegistryWriter.fetch_callback()` will also skip the
downstream push.

#### TLS considerations

For our use case, occystrap binds to `127.0.0.1` and relies on Docker's
implicit insecure trust for `127.0.0.0/8`. No TLS needed.

### Phase 2: Concurrent image support

**Goal**: Handle multiple images being pushed simultaneously (e.g.,
kolla-build pushing several images in parallel).

This requires:

- Per-repository upload tracking (the V2 API already namespaces uploads
  by repository name in the URL path)
- Manifest processing in a thread pool (one image's filter pipeline
  should not block another's)
- Backpressure: if the downstream registry is slow, limit how many
  images are being processed concurrently (a semaphore on the thread
  pool)
- Blob reference counting: shared blobs between concurrent pushes
  must not be deleted until all referencing manifests are processed

```python
# After manifest arrives, snapshot which blobs it references
manifest_blobs = set()
for layer in manifest['layers']:
    manifest_blobs.add(layer['digest'].split(':')[1])
manifest_blobs.add(manifest['config']['digest'].split(':')[1])
```

Only delete blobs when no pending manifest references them.

Note: even without Phase 2, Docker pushes a single image's *layers*
in parallel to the embedded registry -- the existing
`ThreadingHTTPServer` already handles that. Phase 2 is about
processing multiple *images'* manifests concurrently.

### Phase 3: Pull-through / full proxy

**Goal**: Also serve pulls, making occystrap a full filtering proxy.

When a client pulls from occystrap:

1. occystrap pulls from the upstream registry
2. Applies filters
3. Serves the filtered result to the client
4. Optionally caches the filtered layers

This is a significantly larger scope and may warrant a separate design.

## Design Decisions

1. **Manifest list handling**: Reject manifest lists initially (same as
   `dockerpush://` today). kolla-build produces single-platform images.
   Revisit if multi-arch support is needed later.

2. **Garbage collection**: With blocking manifest processing (Phase 1),
   blob cleanup is deterministic -- delete after the referencing
   manifest is processed. No timeout needed. Phase 2 (concurrent)
   adds reference counting.

3. **Authentication**: Network-level only (localhost binding). No
   token-based auth for the initial implementation.

4. **Catalog API**: Not implemented. Docker push does not query it.

5. **Error propagation**: Blocking manifest processing means the HTTP
   response reflects the downstream result: 201 on success, 500 on
   failure. Docker reports the failure to the pushing client.

6. **Resumability**: Not needed for initial version. If occystrap
   crashes, re-push. kolla-build can be configured to retry.

## Open Questions

1. **Repository name rewriting**: Is pass-through sufficient for all
   use cases, or do we need `--prefix`/`--strip-prefix` flags from
   the start?

2. **Graceful shutdown**: When the proxy receives SIGTERM, should it
   finish processing the current image before exiting, or abort
   immediately? (Finish is safer but may delay shutdown.)

## Implementation Priority

Phase 1 (persistent proxy with sequential processing) is the MVP. It
delivers the two key wins for Kolla: build/push overlap, and
cross-image layer dedup via the shared `LayerCache`. Sequential
processing is acceptable because the first few images pay the full
cost (uploading base layers) and subsequent images are fast (cache
hits for shared layers).

Phase 2 (concurrent image support) is valuable for CI/CD pipelines
that push multiple images in parallel, but not essential for the
initial Kolla use case.

Phase 3 (pull-through) is a separate project.

## Files Likely Affected

| File | Changes |
|------|---------|
| `occystrap/proxy.py` | **New**: persistent registry server, reuses `EmbeddedRegistryHandler` patterns from `dockerpush.py` but with blocking manifest processing and no Docker Engine API coupling |
| `occystrap/main.py` | New `proxy` subcommand with `--listen`, `--downstream`, `--layer-cache`, `-f` flags |
| `occystrap/pipeline.py` | Possibly minor changes to expose `build_pipeline()` for use with a synthetic input; may need no changes if the proxy builds pipelines directly |
| `occystrap/inputs/dockerpush.py` | Unchanged (existing `dockerpush://` input continues to work as before) |
| Tests, docs | Corresponding updates |

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* There are unit and functional tests for these features.
* Unit and functional tests pass.
* Documentation in `docs/` has been updated to describe these
  new features and how we use them.

### Future work

* Phase 2: concurrent image support (thread pool, blob refcounting,
  backpressure)
* Phase 3: pull-through proxy (separate design)
* Manifest list (multi-arch) support
* Repository name rewriting (`--prefix`/`--strip-prefix`)
* Token-based authentication for non-localhost deployments
* TLS support or reverse proxy documentation
* Graceful shutdown (finish current image on SIGTERM)

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
