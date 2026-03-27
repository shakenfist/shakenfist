# Performance Tuning

Occystrap provides several flags to control parallelism and connection
behaviour. This guide explains what each flag does, when to adjust it, and
what values work best for common scenarios.

## Parallelism Flags

### `-j` / `--parallel` (default: 4)

Controls **per-image layer download parallelism**. When fetching an image
from a registry, occystrap downloads up to `-j` layers simultaneously using
a thread pool.

Higher values help when:
- The registry has high latency (each request takes a long time to start).
- The image has many small layers that individually don't saturate your
  bandwidth.

Diminishing returns above 8 for most registries — the bottleneck shifts to
bandwidth or registry-side rate limits.

**Also affects:** Quay API tag resolution (parallel `has_tag()` checks) and
registry output compression/upload threads.

### `-J` / `--image-parallel` (default: 3)

Controls **multi-image concurrency**. When processing bulk operations (e.g.,
`quay://org/*:tag` resolving to many images), occystrap processes up to `-J`
images simultaneously.

Each image uses its own `-j` thread pool, so the total concurrent
connections is approximately `-j × -J`.

Higher values help when:
- You're processing many small images (the per-image overhead dominates).
- Your disk and network can handle the additional concurrent I/O.

Lower values are safer when:
- The registry has strict rate limits.
- Images are very large (memory pressure from concurrent downloads).

### `--rate-limit` (default: none)

Sets a **requests-per-second cap** across all threads. When set, occystrap
limits the rate of HTTP requests to avoid triggering registry rate limits
(HTTP 429).

Recommended starting values:
- **Docker Hub:** 2.0 (anonymous) or 5.0 (authenticated)
- **Quay.io:** 5.0
- **Private registries:** usually not needed

### `--retries` (default: 3)

Controls how many times occystrap retries a failed request with
**exponential backoff**. Each retry waits longer (2^attempt seconds).

Increase to 5-7 for unreliable networks or registries that occasionally
return transient errors.

## Connection Efficiency

Occystrap uses [httpx](https://www.python-httpx.org/) with HTTP/2 support.
This provides:

- **Connection pooling** — a single TLS connection is reused for multiple
  requests to the same registry, eliminating repeated TLS handshakes.
- **HTTP/2 multiplexing** — multiple requests can be in-flight on the same
  connection, reducing head-of-line blocking.
- **Automatic protocol negotiation** — httpx negotiates HTTP/2 via ALPN
  where the server supports it, falling back to HTTP/1.1 transparently.

These optimisations apply automatically. No user configuration is needed.

## Output-Specific Behaviour

### `dir://` output

When the temp directory and output directory are on the same filesystem,
occystrap uses `os.rename()` to move downloaded layers into place instead of
copying. This makes the layer write effectively instant (O(1) metadata
operation instead of O(n) data copy).

To ensure this optimisation applies, either:
- Don't set `--temp-dir` (defaults to the system temp, which is usually the
  same filesystem), or
- Set `--temp-dir` to a path on the same filesystem as the output directory.

### `registry://` output

Registry push already parallelises compression and upload. Each layer is
compressed and uploaded in a background thread. The `-j` flag controls the
thread pool size for this.

### `tar://` and `docker://` output

These outputs are inherently sequential (single stream). Layer downloads
still happen in parallel via `-j`, but the output writes one layer at a
time. Performance is bounded by download speed and sequential write
throughput.

## Recommended Settings

| Scenario | `-j` | `-J` | `--rate-limit` | Notes |
|----------|------|------|----------------|-------|
| Single image from private registry | 4 | 1 | — | Default works well |
| Single large image (many layers) | 8 | 1 | — | More parallelism helps |
| Bulk Quay.io mirror (many images) | 4 | 6 | 5.0 | Rate limit avoids 429s |
| Bulk Docker Hub mirror | 4 | 3 | 2.0 | Docker Hub is strict |
| Mirror to private registry | 8 | 3 | — | Push parallelism helps |
| Airgap preparation (registry→tar) | 4 | 1 | — | Tar is sequential anyway |
| High-latency registry (satellite) | 16 | 3 | — | Overlap hides latency |
| Low-bandwidth link | 2 | 1 | — | Avoid contention |

## Benchmarking

A benchmark script is provided at `tools/benchmark.sh`. It runs
representative workflows against a local Docker registry with varying `-j`
and `-J` values and reports wall-clock timing.

### Prerequisites

```bash
# Start a local registry
docker run -d -p 5000:5000 --name registry registry:2

# Populate with test images
for img in busybox ubuntu hello-world; do
    docker pull "$img:latest"
    docker tag "$img:latest" "localhost:5000/library/$img:latest"
    docker push "localhost:5000/library/$img:latest"
done
```

### Running benchmarks

```bash
# TSV output (default)
tools/benchmark.sh

# JSON output
tools/benchmark.sh --json

# Multiple runs for more stable results
tools/benchmark.sh --runs 3

# Custom registry
tools/benchmark.sh --registry myregistry:5000
```

### Interpreting results

The output is a table with columns: `workflow`, `j`, `J`, `wall_s`,
`exit_code`. Compare `wall_s` across different `-j`/`-J` values for the
same workflow to find the optimal settings for your environment.

Benchmarks measure raw transfer performance with no layer cache. Real-world
performance will be faster when the layer cache (`--layer-cache`) is
enabled, as duplicate layers are skipped.

## Verification overhead

Post-write verification (`--verify`, enabled by default) adds negligible
overhead for most output types — it checks file existence and sizes, which
are metadata-only operations. For `registry://` output, verification adds
one HEAD request per blob plus one GET for the manifest.

`--verify-full` re-reads and validates all layer data, which roughly doubles
the disk I/O for `dir://` and `tar://` output. Use `--no-verify` if
verification overhead is a concern in high-throughput CI pipelines where
correctness is validated downstream.
