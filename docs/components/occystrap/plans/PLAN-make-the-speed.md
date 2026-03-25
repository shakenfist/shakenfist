# Make the speed: occystrap performance overhaul

## Prompt

Before responding to questions or discussion points in this
document, explore the occystrap codebase thoroughly. Read relevant
source files, understand existing patterns (pipeline architecture,
input/filter/output interfaces, URI parsing, CLI commands, registry
authentication, error handling), and ground your answers in what
the code actually does today. Do not speculate about the codebase
when you could read it instead. Where a question touches on
external concepts (Docker Registry V2, OCI specs, container image
formats, compression), research as needed to give a confident
answer. Flag any uncertainty explicitly rather than guessing.

Consult `ARCHITECTURE.md` for the pipeline pattern, element types,
input/filter/output interfaces, and cross-cutting concerns (layer
caching, parallel downloads, compression). Consult `CLAUDE.md` for
build commands and project conventions.

When we get to detailed planning, I prefer a separate plan file
per detailed phase. These separate files should be named for the
master plan, in the same directory as the master plan, and simply
have `-phase-NN-descriptive` appended before the `.md` file
extension. Tracking of these sub-phases should be done via a table
like this in this master plan under the Execution section:

```
| Phase | Plan | Status |
|-------|------|--------|
| 1. Registry listing API | PLAN-thing-phase-01-listing.md | Not started |
| 2. Label filtering | PLAN-thing-phase-02-labels.md | Not started |
| ...   | ...  | ...    |
```

I prefer one commit per logical change, and at minimum one commit
per phase. Do not batch unrelated changes into a single commit.
Each commit should be self-contained: it should build, pass tests,
and have a clear commit message explaining what changed and why.

## Situation

Jack has (correctly) observed that occystrap is slow. While the
most visible case is bulk mirroring via `quay://` URIs (e.g.
mirroring 50+ OpenStack Kolla images), the performance problems
are systemic and affect every source/destination combination.

The motivating example:

```
occystrap process \
    quay://openstack.kolla/*:2025.1-debian-bookworm?since=2026-03-01 \
    dir://openstack-kolla-2025.1-debian-bookworm?unique_names=true
```

### Bottleneck inventory by pipeline stage

**HTTP layer (affects all network sources and destinations):**

1. **No HTTP connection pooling.** Every HTTP request in
   `util.request_url()` calls `requests.request()` directly,
   creating a fresh TCP connection (and TLS handshake) each time.
   For a single image with 10 layers, that's ~12 separate TLS
   handshakes to the same registry. For 50 images, hundreds.
   This affects `registry://` input, `registry://` output, and
   all Quay API calls.

2. **No HTTP/2.** The `requests` library only supports HTTP/1.1.
   HTTP/2 multiplexing would allow multiple concurrent requests
   over a single connection, eliminating head-of-line blocking
   and reducing connection overhead. This matters for any
   registry interaction (input, output, or proxy).

**Source-side bottlenecks:**

3. **Sequential Quay API tag resolution.** `resolve_quay_uri()`
   checks `has_tag()` for each matching repository one at a time.
   With 100+ repos in a namespace, this resolution phase alone
   can take minutes.

4. **Registry input head-of-line blocking.** When `ordered=True`
   (required by some outputs), early slow layers block later
   fast ones from being yielded. The layers download in the
   background but the pipeline stalls waiting for layer 0 even
   if layers 1-9 are already complete.

5. **Docker daemon input is inherently serial.** `docker://`
   input fetches the entire image as a single tarball via the
   Docker Engine API — there is no per-layer endpoint. This is
   a Docker API limitation, not something we can fix, but we
   should ensure we don't add overhead on top of it.

**Pipeline orchestration bottlenecks:**

6. **Sequential multi-image processing.** `_process_multi()` in
   `main.py` loops over resolved images one at a time. While
   layers within a single image download in parallel (up to `-j`
   threads), only one image is active at any given moment. For
   50 images this means 50× serial pipeline constructions.
   This affects every multi-image workflow regardless of source
   or destination.

7. **No input/output overlap.** The pipeline processes elements
   synchronously: each element is fetched, then immediately
   processed by the output. While this is fine for streaming
   within a single layer, it means the output cannot work ahead
   (e.g. writing layer N to disk while layer N+1 downloads).

**Destination-side bottlenecks:**

8. **Directory I/O is single-threaded.** `DirWriter` writes each
   layer file sequentially. For large images or SSDs capable of
   parallel writes, this leaves bandwidth on the table.

9. **Registry output blob-exists checks are per-layer.** Before
   uploading each layer, `RegistryWriter` does a HEAD request
   to check if the blob already exists. These checks happen
   inside the thread pool but could be batched or pipelined
   more aggressively.

10. **Tarball output is inherently sequential.** `TarWriter`
    appends to a single tar stream, so parallelism is not
    possible within the tar write itself. However, the input
    side can still benefit from concurrent downloads feeding
    into the sequential tar writer.

### Impact matrix by source × destination

| Source ↓ \ Dest → | `dir://` | `tar://` | `registry://` | `oci://` / `mounts://` | `docker://` |
|--------------------|----------|----------|---------------|------------------------|-------------|
| `registry://`      | #1,#4,#7,#8 | #1,#4 | #1,#4,#9 | #1,#4,#7,#8 | #1,#4 |
| `quay://` (bulk)   | #1,#3,#4,#6,#7,#8 | N/A | #1,#3,#4,#6,#9 | N/A | N/A |
| `tar://`           | #7,#8 | — | #9 | #7,#8 | — |
| `docker://`        | #5,#7,#8 | #5 | #5,#9 | #5,#7,#8 | — |
| `dockerpush://`    | #7,#8 | — | #9 | #7,#8 | — |

Items #2 (HTTP/2) and #10 (tar sequential) are cross-cutting
or structural limitations noted for context.

## Mission and problem statement

Make occystrap fast enough that Jack has nothing to complain about.
Systematically address performance bottlenecks across all source
and destination combinations by exploiting every reasonable form
of parallelism and connection efficiency available, while
preserving correctness and the existing pipeline architecture.

The highest-impact scenario is bulk `quay://` operations (which
hit nearly every bottleneck), but single-image `registry://` →
`dir://` and `registry://` → `registry://` transfers should also
benefit substantially from connection pooling and I/O
improvements.

The changes should be invisible to users except that things finish
faster. Existing CLI flags, URI schemes, and output formats must
continue to work identically. The `-j` flag should continue to
control parallelism (though its scope will expand).

## Open questions

1. **httpx vs requests+urllib3 session pooling.** ~~Switching to
   `httpx` gives us HTTP/2 for free, but it's a new dependency
   and its streaming API differs from `requests`. An alternative
   is to use `requests.Session` with connection pooling (which
   keeps HTTP/1.1 but eliminates repeated TLS handshakes) and
   defer HTTP/2 to a later phase. Which approach do we prefer?~~

   *Decision:* Go big. Phase 1 replaces `requests` with `httpx`
   for all registry HTTP, giving us connection pooling and HTTP/2
   in one shot. The `requests_unixsocket` usage for Docker daemon
   communication (local Unix socket in `inputs/docker.py`,
   `inputs/dockerpush.py`, `outputs/docker.py`) stays on
   `requests` for now since those are local calls where HTTP/2
   provides no benefit. This can be migrated to `httpx` custom
   transports in future work if desired.

2. **Multi-image concurrency model.** ~~Should we use threads
   (ThreadPoolExecutor), asyncio, or multiprocessing for parallel
   image processing?~~

   *Decision:* ThreadPoolExecutor for image-level concurrency,
   consistent with existing layer-level parallelism. The
   bottleneck is I/O (network + disk), so the GIL is not a
   concern. A new `--image-parallel` / `-J` flag controls this
   separately from per-image layer parallelism (`-j`).

3. **Progress reporting with concurrent images.** ~~The current
   tqdm progress bars assume one image at a time. With multiple
   images in flight, we need either per-image bars or an
   aggregate bar.~~

   *Decision:* Two-level reporting. Use image completion as the
   coarse progress unit (e.g. `[3/50] images complete`) with
   per-image layer progress as secondary log lines. This avoids
   the problem of predicting total work upfront — the image
   count is known from Quay resolution, and per-image layer
   counts are reported as each image progresses.

4. **Layer cache contention.** ~~With multiple images writing to
   the same `LayerCache` concurrently, we need thread-safe
   access.~~

   *Decision:* Add a `threading.Lock` around cache reads and
   writes. The cache is small JSON so contention will be
   minimal. Implemented in Phase 3 alongside multi-image
   concurrency.

5. **Rate limiting.** ~~Quay.io and Docker Hub have rate limits.
   Aggressive parallelism could trigger 429 responses.~~

   *Decision:* Implement in Phase 1 alongside the httpx
   migration:
   - Add `--rate-limit` flag (requests per second) to throttle
     outgoing HTTP requests. Applied globally across all threads
     via a token bucket or simple semaphore in the httpx client
     layer.
   - Add `--retries` flag (default: 3, matching current
     `MAX_RETRIES`) to control how many times to retry on 429
     and 5xx responses with exponential backoff. This replaces
     the hardcoded `MAX_RETRIES = 3` in `util.py`.
   - Retry-on-429 should respect the `Retry-After` header when
     present, falling back to exponential backoff when absent.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Replace requests with httpx | PLAN-make-the-speed-phase-01-httpx.md | Complete |
| 2. Parallel Quay API resolution | PLAN-make-the-speed-phase-02-parallel-resolution.md | Complete |
| 3. Concurrent multi-image processing | PLAN-make-the-speed-phase-03-multi-image-concurrency.md | Complete |
| 4. Parallel and async output I/O | PLAN-make-the-speed-phase-04-parallel-output-io.md | Complete |
| 5. Benchmarking and tuning | PLAN-make-the-speed-phase-05-benchmarking.md | Complete |
| 6. Processing summary | (inline) | Complete |

### Phase 1: Replace requests with httpx

**Goal:** Replace the `requests` library with `httpx` for all
registry HTTP communication, gaining connection pooling, HTTP/2
multiplexing, and a modern async-capable client in one step.

**Scope — what changes:**
- `util.py:request_url()` — rewrite to use `httpx.Client`
  (sync) instead of `requests.request()`. This is the central
  HTTP function used by `inputs/registry.py` for manifest,
  config, and layer downloads.
- `outputs/registry.py:RegistryWriter` — has its own direct
  `requests.request()` calls (lines 129, 151) separate from
  `util.request_url()`. Migrate these to use a shared
  `httpx.Client`.
- `quay.py:QuayClient` — uses `util.request_url()`, so inherits
  the migration automatically.
- `proxy.py` — creates its own `inputs/registry.py:Image` and
  `outputs/registry.py:RegistryWriter` instances, so inherits
  the migration automatically.

**Scope — what stays on requests:**
- `inputs/docker.py` — uses `requests_unixsocket` for Docker
  daemon communication over a local Unix socket. No benefit
  from HTTP/2; migrate later if desired.
- `inputs/dockerpush.py` — same, uses `requests_unixsocket`
  for the embedded push server's Docker daemon calls.
- `outputs/docker.py` — same, uses `requests_unixsocket` for
  the `/images/load` endpoint.

**Changes:**
- Add `httpx[http2]` to dependencies in `pyproject.toml`.
  Keep `requests` and `requests-unixsocket` for Docker daemon
  code.
- Create `util.create_client()` factory that returns an
  `httpx.Client` configured with:
  - HTTP/2 enabled (`http2=True`)
  - Connection pooling (`limits=httpx.Limits(
    max_connections=20, max_keepalive_connections=10)`)
  - Configurable timeout
  - User-Agent header set globally on the client
- Rewrite `util.request_url()` to accept an `httpx.Client`
  parameter. When provided, use the client. When not provided,
  create a one-shot client (backwards compat for any callers
  not yet migrated).
- Adapt streaming: `requests` uses `r.iter_content(chunk_size)`
  while `httpx` uses `r.stream()` context manager with
  `response.iter_bytes(chunk_size)`. Update all streaming
  download paths in `inputs/registry.py:_download_layer()`.
- Adapt error handling: replace `requests.exceptions` imports
  (`ChunkedEncodingError`, `ConnectionError`) with `httpx`
  equivalents (`httpx.StreamError`, `httpx.ConnectError`,
  `httpx.RemoteProtocolError`).
- Adapt response API differences: `r.text` stays the same,
  `r.status_code` stays the same, `r.headers` stays the same.
  `r.content` stays the same. Main difference is streaming.
- Add `--rate-limit` CLI flag (requests per second) to throttle
  outgoing HTTP requests across all threads. Implement as a
  token bucket or semaphore in the client layer.
- Add `--retries` CLI flag (default: 3) to replace the
  hardcoded `MAX_RETRIES` in `util.py`. Controls retry count
  for 429 and 5xx responses with exponential backoff.
- Implement retry logic in `util.request_url()` for 429 and
  5xx responses. Respect `Retry-After` header on 429 when
  present, fall back to exponential backoff when absent.
  httpx does not have built-in retry (unlike urllib3's `Retry`),
  so this is custom logic in our code.
- Have `inputs/registry.py:Image` create an `httpx.Client` in
  `__init__` and pass it through all calls. Close it when done.
- Have `outputs/registry.py:RegistryWriter` create its own
  `httpx.Client` in `__init__` for upload connections.
- Have `quay.py:QuayClient` create a client in `__init__`.
- Update all ~25 test mocks that patch `requests.request` to
  patch the equivalent httpx calls instead.

**Key httpx differences to handle:**
- `httpx` raises `httpx.HTTPStatusError` on non-2xx when using
  `response.raise_for_status()`, but we do manual status
  checking, so this is not an issue.
- `httpx` responses are not automatically decoded; `.json()`
  works the same but `.text` is a property (same as requests).
- `httpx.Client` is a context manager and should be closed;
  use `__del__` or explicit `.close()` in Image/Writer classes.
- HTTP/2 is negotiated via ALPN during TLS — if the registry
  doesn't support it, httpx falls back to HTTP/1.1 silently.
  No special handling needed.

**Risk:** Medium. This is a larger change than session pooling
alone, but httpx's sync API is deliberately requests-compatible.
The main risk is in streaming paths and test mock updates. The
payoff is connection pooling + HTTP/2 in a single migration
rather than two separate phases.

**Benefit scope:** Every pipeline involving `registry://` input
or output, `quay://` resolution, and proxy operations. HTTP/2
multiplexing is especially valuable for the parallel layer
download path where multiple large GETs to the same registry
can share a single connection.

### Phase 2: Parallel Quay API resolution

**Goal:** Resolve tags across repositories concurrently instead
of sequentially.

**Changes:**
- In `quay.py:resolve_quay_uri()`, after listing repositories
  and filtering by glob, use `ThreadPoolExecutor` to check
  `has_tag()` across all matching repos concurrently.
- Share the `QuayClient`'s `httpx.Client` across threads
  (httpx clients are thread-safe with connection pooling).
- Default concurrency matches `-j` flag.

**Risk:** Low. Each `has_tag()` call is independent and
read-only.

### Phase 3: Concurrent multi-image processing

**Goal:** Process multiple images in parallel when using
`quay://` bulk sources, with thread-safety for all destination
types that support multi-image use.

**Changes:**
- Refactor `_process_multi()` in `main.py` to use
  `ThreadPoolExecutor` for image-level concurrency.
- Add a `--image-parallel` / `-J` flag (default: 3) to control
  how many images process simultaneously.
- Thread-safety audit for each output type used in multi-image
  workflows:
  - `DirWriter` with `unique_names=true`: each image writes its
    own manifest file, but `catalog.json` needs locking at
    finalize time. `fetch_callback` reads shared layer dirs for
    dedup — needs synchronization.
  - `RegistryWriter`: each image pushes to a different repo, so
    mostly independent. `httpx.Client` from Phase 1 is
    thread-safe. Layer cache needs locking (see below).
  - `TarWriter`: not supported for multi-image (would overwrite),
    already rejected in `_process_multi()`.
- Make `LayerCache` thread-safe with a `threading.Lock`.
- Rework progress reporting to handle concurrent images:
  use per-image log lines and an aggregate summary.
- Share the `httpx.Client` from Phase 1 across concurrent
  images targeting the same registry, so HTTP/2 multiplexing
  and connection pooling are most effective.

**Risk:** Medium. Multi-image concurrency touches the pipeline
orchestration layer. Careful attention needed to shared state
(catalog.json, layer cache, progress bars). Test thoroughly
with both `dir://?unique_names=true` and `registry://` output.

### Phase 4: Parallel and async output I/O

**Goal:** Parallelize I/O in output writers where the format
allows it, and pipeline input/output overlap where possible.

**Changes for `dir://` output (`DirWriter`):**
- In `process_image_element()`, submit layer writes to a thread
  pool instead of writing synchronously.
- Track futures and wait for all writes in `finalize()` before
  writing the manifest.
- Support out-of-order layer arrival (already partially
  supported via `_indexed_layers`).
- Same approach for `OCIBundleWriter` and `MountWriter`, which
  both extend `DirWriter`.

**Changes for `registry://` output (`RegistryWriter`):**
- Already uses a ThreadPoolExecutor for compression+upload, but
  blob-exists HEAD checks happen synchronously before each
  submission. Batch or pipeline these checks so the thread pool
  stays saturated.

**Changes for `tar://` output (`TarWriter`):**
- Tar is inherently sequential (single stream), so no parallel
  writes. However, ensure the input side can buffer-ahead so
  downloads proceed while the tar writer is busy appending.

**Changes for `docker://` output (`DockerWriter`):**
- Uses the Docker Engine API's `/images/load` endpoint which
  accepts a single tar stream. Same constraint as `TarWriter`.
  No parallelism in the write, but input-side buffering helps.

**Risk:** Low for directory-based outputs (independent files).
Registry output changes are refinements to existing parallelism.
Tar/docker outputs get indirect benefit from input-side overlap.

### Phase 5: Benchmarking and tuning

**Goal:** Measure improvements across all source/destination
combinations and tune defaults.

**Changes:**
- Create a benchmarking script that tests representative
  workflows:
  - Bulk `quay://` → `dir://` (the motivating case)
  - Single `registry://` → `dir://` (common single-image pull)
  - Single `registry://` → `registry://` (mirror/push)
  - `tar://` → `dir://` (offline extraction)
  - `registry://` → `tar://` (airgap preparation)
- Measure wall-clock time, connection count (via debug logs),
  and CPU utilisation for each.
- Profile to find any remaining bottlenecks.
- Tune default thread pool sizes based on measurements.
- Document performance characteristics and tuning guidance
  in `docs/`, covering which `-j`/`-J` values work best for
  different scenarios (bandwidth-limited vs latency-limited
  vs CPU-limited).

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* The code passes `flake8 --max-line-length=120` and
  `pre-commit run --all-files`.
* New code follows the existing pipeline pattern (input/filter/
  output interfaces) where applicable.
* There are unit tests for core logic and integration tests for
  new CLI commands.
* Lines are wrapped at 120 characters, single quotes for strings,
  double quotes for docstrings.
* Documentation in `docs/` has been updated to describe any new
  commands or features.
* `ARCHITECTURE.md`, `README.md`, and `AGENTS.md` have been
  updated if the change adds or modifies modules or CLI commands.
* All source/destination combinations are at least as fast as
  before (no regressions from added overhead).
* Bulk `quay://` → `dir://` operations show 3-5× improvement
  for 50+ image bulk operations.
* Single-image `registry://` → `dir://` shows measurable
  improvement from httpx connection pooling and HTTP/2.
* `registry://` → `registry://` shows improvement from httpx
  client reuse on both input and output sides.
* Existing tests continue to pass with no regressions.
* The `-j` flag continues to work as before.

### Future work

* **Async pipeline with httpx.AsyncClient.** Phase 1 uses
  `httpx.Client` (sync) to minimize disruption. A future phase
  could migrate to `httpx.AsyncClient` with asyncio, replacing
  ThreadPoolExecutor with true async I/O. This would be most
  valuable if benchmarks show thread contention limiting
  throughput at high `-j` values.
* **Migrate Docker daemon code to httpx.** Replace
  `requests_unixsocket` with httpx custom transports for Unix
  socket communication. Low priority since these are local
  calls, but would allow dropping `requests` entirely.
* **Streaming pipeline overlap.** Currently each pipeline stage
  completes before the next begins. A producer-consumer queue
  between input and output could allow download and write to
  overlap at the element level, not just the layer level.
* **Compressed layer passthrough.** When no content-modifying
  filters are applied, layers could be written in their
  compressed form and decompressed only on read. This saves
  CPU and temp disk space.
* **Registry-to-registry direct transfer.** For `registry://` →
  `registry://` without filters, blob mounts could transfer
  layers without downloading them at all.
* **Adaptive rate limiting.** The `--rate-limit` flag from
  Phase 1 is static. A future enhancement could auto-tune the
  rate based on 429 response frequency.

### Bugs fixed during this work

(None yet.)

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
