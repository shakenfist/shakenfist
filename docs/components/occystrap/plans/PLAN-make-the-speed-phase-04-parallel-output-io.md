# Phase 4: Parallel and async output I/O

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

I prefer one commit per logical change, and at minimum one commit
per phase. Do not batch unrelated changes into a single commit.
Each commit should be self-contained: it should build, pass tests,
and have a clear commit message explaining what changed and why.

## Goal

Make the output side of the pipeline non-blocking where possible,
so that download and write operations overlap rather than
serializing.

## Current state analysis

### The pipeline bottleneck

`_fetch()` in `main.py` drives the pipeline:

```python
for element in img.fetch(ordered=ordered):
    output.process_image_element(element)
output.finalize()
```

This is a synchronous loop: the input yields an element,
the output processes it, then the input yields the next
one. The input (`registry.Image.fetch()`) downloads layers
in parallel via its own `ThreadPoolExecutor`, but it can
only yield the next completed layer after the output
finishes processing the current one.

### What each output does in process_image_element()

| Output | What happens | Blocking? | Parallelizable? |
|--------|-------------|-----------|-----------------|
| `DirWriter` | Copies layer from temp file to output dir | Yes, disk I/O | Yes — independent files |
| `TarWriter` | Appends layer to tar stream | Yes, disk I/O | No — single sequential stream |
| `DockerWriter` | Builds tar in memory, posts to daemon | Yes, network | No — single API call |
| `RegistryWriter` | Reads data, submits to thread pool | **No** — returns immediately | Already parallelized |

**Key finding: `RegistryWriter` is already non-blocking.**
It reads layer data into memory, submits compress+upload to
its own thread pool, and returns. The pipeline loop can
immediately yield the next element. The blob-exists check
runs inside the thread pool, not on the main thread. **No
changes needed for `RegistryWriter`.**

### DirWriter opportunity

`DirWriter.process_image_element()` copies the layer from a
temp file to the output directory (100KB reads in a loop).
This blocks the `_fetch()` loop. On SSDs, parallel writes
would be faster; on HDDs, they might be slower.

However, there's a lifecycle constraint: `element.data` is a
file handle to a temp file. After `process_image_element()`
returns, the `_fetch()` loop's caller (`inputs/registry.py`)
closes the file handle and deletes the temp file. If we
submit the write to a thread pool and return immediately,
the temp file gets deleted while the write is still in
progress.

To fix this, we need to either:
1. Read the entire layer into memory before submitting (uses
   more memory), or
2. Copy the temp file to a new temp location that the writer
   thread owns (double I/O), or
3. Change the lifecycle contract so the output owns the temp
   file (bigger refactor).

Option 1 is impractical for large layers (hundreds of MB).
Option 2 adds overhead that may negate the parallelism
benefit. Option 3 changes the pipeline contract.

### A better approach: non-blocking DirWriter via data ownership

The cleanest approach is option 3 with minimal disruption:
have `DirWriter.process_image_element()` read the data from
`element.data` into its own buffer (or temp file), then
submit the disk write to a thread pool. The input's temp
file can be closed immediately.

But wait — the data is already in a temp file, and
`DirWriter` writes it to its final location. Making DirWriter
read the data into memory just to write it back out adds a
full copy. The current approach of streaming from one file
to another is already efficient.

### Reassessment

The real question is: **does the synchronous DirWriter write
actually bottleneck the pipeline?**

With `registry://` input:
1. Input downloads layers in parallel (up to `-j` threads).
2. Input yields completed layers one at a time.
3. DirWriter writes each layer to disk.
4. While DirWriter writes, input threads continue
   downloading other layers in the background.

The input's ThreadPoolExecutor is *not* blocked by the
output write. It continues downloading in background
threads. The only thing blocked is the *yield* — the next
completed download can't be yielded until the current
write finishes. But if downloads are slower than disk
writes (likely — network is slower than SSD), there's no
write to yield anyway.

**The DirWriter write is only a bottleneck when:**
- Multiple layers finish downloading simultaneously, AND
- Disk I/O is slower than the download rate

This scenario is uncommon. For most workloads, the network
is the bottleneck, not the disk. And with Phase 3's
multi-image concurrency, the disk is already being used by
multiple concurrent images.

### TarWriter and DockerWriter

Both are inherently sequential (single stream). No
parallelism possible. Input-side buffering doesn't help
because the input already downloads in parallel — the yield
just waits for the output to consume the current element.

### Conclusion

After careful analysis, the Phase 4 changes in the master
plan have diminishing returns:

| Change | Effort | Benefit |
|--------|--------|---------|
| DirWriter parallel writes | Medium | Low — disk rarely bottlenecks |
| RegistryWriter blob-exists batching | N/A | Already non-blocking |
| TarWriter input buffering | Low | None — inherently sequential |
| DockerWriter input buffering | Low | None — inherently sequential |

The high-impact optimizations were in Phases 1-3:
connection pooling, HTTP/2, parallel tag resolution, and
multi-image concurrency. Phase 4 is refinement with
limited payoff.

## Recommended implementation

Given the analysis, Phase 4 should be scoped down to a
single, simple optimization that provides measurable
benefit without complexity:

### Non-blocking DirWriter via shutil.move

Instead of copying layer data from the temp file to the
output directory (read + write), use `shutil.move()` (or
`os.rename()` if same filesystem) to atomically move the
temp file to its final location. This eliminates the
copy entirely — the layer write becomes O(1) instead of
O(n).

Currently the pipeline is:
1. Input downloads to temp file
2. Input yields element with file handle to temp file
3. DirWriter reads from temp file, writes to output dir
4. Input deletes temp file

With `shutil.move()`:
1. Input downloads to temp file
2. Input yields element with temp file *path*
3. DirWriter moves temp file to output dir (instant on
   same filesystem)
4. No delete needed — file was moved

This requires a small change to the element data contract:
for DirWriter, pass the temp file path so it can be moved
instead of copied. Other outputs continue reading from the
file handle as before.

### Implementation steps

#### Step 1: Add temp_path to ImageElement

Add an optional `temp_path` field to `constants.ImageElement`
that carries the temp file path alongside the file handle.
Set this in `inputs/registry.py:_download_layer()`.

#### Step 2: Use os.rename in DirWriter when possible

In `DirWriter.process_image_element()`, if `element.temp_path`
is set, use `os.rename()` (wrapped in try/except for
cross-filesystem fallback to the current copy). This skips
the read-write loop entirely.

#### Step 3: Skip temp file deletion in input

In `inputs/registry.py:fetch()`, if `element.temp_path` was
set and the output consumed it (moved the file), skip the
`os.unlink()` of the temp file.

#### Step 4: Update tests and documentation

Update any tests that create ImageElements to include the
new optional field. Update ARCHITECTURE.md.

## Commit plan

1. **Optimize DirWriter layer writes with os.rename.**
   Add `temp_path` to `ImageElement`, use `os.rename()` in
   DirWriter to move layers instead of copying, skip temp
   file deletion when the file was moved. Update docs.

## Risk assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Cross-filesystem rename fails | Low | None | Fallback to current copy behavior |
| temp_path breaks other outputs | None | None | Optional field, other outputs ignore it |
| Expand mode needs the file in place | Low | Low | Only use rename when expand=False |

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
