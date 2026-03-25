# Phase 3: Concurrent multi-image processing

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

Process multiple images concurrently in `_process_multi()`,
reducing wall-clock time for bulk `quay://` operations from
O(n) sequential pipelines to O(n/J) where J is the image-level
parallelism.

## Current state

### _process_multi() (main.py:185-237)

Loops sequentially over resolved images, calling
`_process_single()` for each. Each call creates its own
`PipelineBuilder`, input source, and output writer — fully
independent instances.

### Thread-safety audit results

Each `_process_single()` call is independent:

| Component | Thread-safe? | Notes |
|-----------|-------------|-------|
| `PipelineBuilder` | Yes | Stateless, fresh per call |
| `inputs/registry.py:Image` | Yes | Own httpx.Client, own ThreadPoolExecutor |
| `outputs/registry.py:RegistryWriter` | Yes | Own httpx.Client, own executor, internal locks |
| `outputs/directory.py:DirWriter` | **Mostly** | Own state, but **catalog.json is shared** |
| `layer_cache.py:LayerCache` | Yes | Has `threading.Lock`, atomic save |
| `DirWriter.fetch_callback()` | Yes | Reads filesystem per-image path |
| `DirWriter.process_image_element()` | Yes | Writes to per-image directory |
| `DirWriter.finalize() catalog.json` | **NO** | Read-modify-write without locking |

### The catalog.json race condition

When `unique_names=true`, each image gets its own manifest
file (`manifest-image_tag.json`) but all images share one
`catalog.json` in the root output directory. The
`finalize()` method does a non-atomic read-modify-write:

```python
c = {}
catalog_path = os.path.join(self.image_path, 'catalog.json')
if os.path.exists(catalog_path):
    with open(catalog_path, 'r') as f:
        c = json.loads(f.read())
c.setdefault(self.image, {})
c[self.image][self.tag] = manifest_filename
with open(catalog_path, 'w') as f:
    f.write(json.dumps(c, indent=4, sort_keys=True))
```

If two images finalize concurrently, the last writer
overwrites the first's entry. This must be fixed with a
file lock.

### DirWriter layer deduplication

`fetch_callback()` checks `os.path.exists(layer_path)` to
skip layers already on disk. With concurrent images sharing
base layers, two images could try to write the same layer
simultaneously. However, each layer is written to a unique
digest-named directory, and the write is to a temp file
followed by rename — so this is safe (last writer wins with
identical content).

### _info_multi() (main.py:611)

Also loops sequentially over quay images. This is read-only
(fetches manifest + config, no downloads) and could benefit
from the same concurrency pattern, but is lower priority.
Include it for consistency.

## Implementation steps

### Step 1: Add --image-parallel / -J CLI flag

Add to `main.py:cli()`:

```python
@click.option('--image-parallel', '-J', default=3,
              type=int, envvar='OCCYSTRAP_IMAGE_PARALLEL',
              help='Number of images to process in '
              'parallel for bulk operations (default: 3)')
```

Store as `ctx.obj['IMAGE_PARALLEL']`.

Default 3 (not 4) because each image already spawns `-j`
threads internally for layer downloads, so total thread
count is `J * j` (default 3 * 4 = 12).

### Step 2: Fix catalog.json race condition

Add a module-level `threading.Lock` in `directory.py` for
catalog.json writes:

```python
_catalog_lock = threading.Lock()
```

In `finalize()`, wrap the catalog.json read-modify-write
in `with _catalog_lock:`. This is a process-wide lock, so
all DirWriter instances serialize their catalog updates.
The critical section is tiny (read JSON + write JSON), so
contention is negligible.

A file lock (`fcntl.flock`) would be more robust for
multi-process scenarios, but occystrap runs as a single
process, so a threading lock suffices and is simpler.

### Step 3: Refactor _process_multi() for concurrency

Replace the sequential loop with ThreadPoolExecutor:

```python
def _process_multi(ctx, images, source, destination,
                   filters):
    ...validation...

    image_parallel = ctx.obj.get('IMAGE_PARALLEL', 3)
    failed = []
    completed = 0

    def _process_one(i, registry, image, tag):
        source_uri = 'registry://%s/%s:%s' % (
            registry, image, tag)
        _process_single(
            ctx, source_uri, destination, filters)

    with ThreadPoolExecutor(
            max_workers=image_parallel) as executor:
        futures = {}
        for i, (registry, image, tag) in enumerate(
                images):
            future = executor.submit(
                _process_one, i, registry, image, tag)
            futures[future] = (
                '%s/%s:%s' % (registry, image, tag))

        for future in as_completed(futures):
            name = futures[future]
            try:
                future.result()
                completed += 1
                click.echo(
                    '[%d/%d] %s complete'
                    % (completed + len(failed),
                       len(images), name),
                    err=True)
            except Exception as e:
                LOG.error(
                    'Failed to process %s: %s'
                    % (name, e))
                failed.append(name)

    ...summary and exit code...
```

### Step 4: Update progress reporting

Per the decided approach (open question #3 in the master
plan): two-level reporting with image completion as the
coarse progress unit.

- Before starting: log total images and parallelism level.
- As each image completes: `[N/total] image_name complete`
- After all: summary with pass/fail counts.

Remove the per-image `(1/50) Processing ...` line that
prints before processing starts (meaningless with
concurrent execution). Instead, log when each image
finishes.

### Step 5: Apply same pattern to _info_multi()

Optional but consistent: parallelize `_info_multi()` with
the same `IMAGE_PARALLEL` flag. Each info call is
independent (just fetches manifest + config). No shared
state concerns.

### Step 6: Update tests

**test_process_quay_basic**: Currently checks
`mock_process_single.call_count == 2` and inspects
`call_args_list[0]` positionally. With concurrent
execution, call ordering is non-deterministic.

Update to:
- Check `call_count` (still works).
- Use `assertCountEqual` on the set of source URIs passed
  to `_process_single` across all calls.

**New test**: Add a test that verifies the `-J` flag is
accepted and passed to `ctx.obj['IMAGE_PARALLEL']`.

### Step 7: Update documentation

- `ARCHITECTURE.md`: Note multi-image concurrency in the
  pipeline orchestration section.
- `README.md`: Document the `-J` / `--image-parallel` flag.

## Commit plan

1. **Fix catalog.json race condition in DirWriter.**
   Add `_catalog_lock` to `directory.py`, wrap the
   read-modify-write in `finalize()`. This is a
   correctness fix independent of concurrency — the race
   exists even with serial execution if someone runs
   two occystrap processes targeting the same directory.

2. **Add concurrent multi-image processing.**
   Add `-J` flag, refactor `_process_multi()` and
   `_info_multi()` to use ThreadPoolExecutor, update
   progress reporting, update tests, update docs.

## Risk assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| catalog.json corruption | High (without fix) | Medium | Step 2 adds lock |
| Disk I/O contention with high -J | Medium | Low | Default J=3 is conservative |
| Registry rate limiting with J*j threads | Medium | Low | --rate-limit from Phase 1 |
| Progress output interleaving on stderr | Low | Low | Each message is a single click.echo |
| Memory pressure with many concurrent images | Low | Low | Each image streams layers to temp files |

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
