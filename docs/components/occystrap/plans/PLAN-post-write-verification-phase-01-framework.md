# Phase 1: Verification framework and DirWriter verifier

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

Add a `--verify` / `--no-verify` flag to the `process` command,
a `verify()` method on `ImageOutput`, and a concrete
implementation for `DirWriter` that checks the written output
is complete and correct.

## Current state

### DirWriter file layout after finalize()

```
{image_path}/
├── catalog.json
├── manifest-{image}_{tag}.json   (or manifest.json)
├── {config_hash}.json
├── {layer1_hash}/
│   └── layer.tar
├── {layer2_hash}/
│   └── layer.tar
└── ...
```

**Instance variables available after finalize():**
- `self.image_path` — root output directory
- `self.tar_manifest[0]['Config']` — config filename
  (e.g., `abc123.json`)
- `self.tar_manifest[0]['Layers']` — list of layer paths
  (e.g., `['def456/layer.tar', 'ghi789/layer.tar']`)
- `self._manifest_filename()` — returns the manifest
  filename stem (e.g., `manifest` or
  `manifest-docker.io_library_busybox-latest`)

### ImageOutput base class

Abstract base with `_track_element()`, `_total_bytes`,
`_layer_count`. Three abstract methods: `fetch_callback()`,
`process_image_element()`, `finalize()`.

### OCIBundleWriter

Extends DirWriter with `expand=True`. Its `finalize()`
calls `_log_bundle()` and `_log_summary()` but does NOT
write manifest or catalog files. After `write_bundle()`,
layers are extracted to `rootfs/` and layer directories
are removed. Verification must account for this different
layout.

### MountWriter

Direct `ImageOutput` subclass (NOT DirWriter). Has its own
`process_image_element()` and `finalize()` that writes
manifest and catalog. Layers are extracted per-directory
with overlay whiteout handling (xattrs, mknod). After
`write_bundle()`, layers are overlay-mounted.

### CheckResults API

```python
results = CheckResults()
results.error('check_id', 'message')
results.warning('check_id', 'message')
results.info('check_id', 'message')
results.has_errors     # bool
results.error_count    # int
results.warning_count  # int
results.results        # list of dicts
```

### _fetch() and stats flow

`_fetch()` returns a stats dict:
```python
{'bytes': N, 'layers': N, 'retries': N, 'rate_limits': N}
```

Stats feed into `_print_summary()` which outputs:
```
Summary: 47/47 images, 312 layers, 4.2 GB, 38.1s
```

### Where --verify fits in the CLI

Global options are on the `cli` group (lines 33-80 of
main.py). The `--verify` flag should go here so it's
available to `process` and potentially other commands.
Stored in `ctx.obj['VERIFY']`.

## Design

### verify() method on ImageOutput

A **concrete** method (not abstract) with a default no-op
implementation. Returns a `CheckResults` instance. Output
writers override it to add type-specific checks.

```python
# outputs/base.py
def verify(self, full=False):
    """Verify the output is complete and correct.

    Called after finalize(). Returns CheckResults.
    Override in subclasses for type-specific checks.

    Args:
        full: If True, re-read and hash all data.
            If False, only check existence and sizes.
    """
    return CheckResults()
```

The `full` parameter controls the depth:
- `full=False` (default `--verify`): stat files, check
  existence and sizes.
- `full=True` (`--verify=full`): also re-read and SHA256
  hash every layer.

### DirWriter.verify()

Checks performed:

1. **Manifest file exists and is valid JSON.**
   - Path: `{image_path}/{manifest_filename}.json`
   - Parse as JSON, verify it has `Layers` and `Config`
     keys.

2. **Config file exists.**
   - Path: `{image_path}/{tar_manifest[0]['Config']}`

3. **Each layer file exists.**
   - For each entry in `tar_manifest[0]['Layers']`:
     path `{image_path}/{layer_path}` must exist.

4. **Layer file sizes match expectations.**
   - During `process_image_element()`, record each
     layer's size in a new `self._expected_layers` dict
     mapping layer path to size.
   - In `verify()`, `os.path.getsize()` each layer and
     compare.

5. **Full mode: re-read and hash each layer.**
   - Read each layer file in 64KB chunks, compute
     SHA256.
   - Compare against the layer digest (which is the
     directory name).
   - Note: for DirWriter, the layer file is the
     *decompressed* tarball. The directory name is the
     compressed digest from the registry. So
     hash-checking the decompressed file against the
     compressed digest won't match. Instead, just
     verify the file is a valid tarball by opening it
     with `tarfile.open()`.

### OCIBundleWriter.verify()

After `write_bundle()`:
- `rootfs/` directory exists
- `config.json` exists and is valid JSON
- `container-config.json` exists
- Layer directories have been removed

Before `write_bundle()` (if verify runs after finalize
but before write_bundle):
- Same as DirWriter checks, since layers are still on
  disk.

*Decision:* Verify should run after the full pipeline
including `write_bundle()`. So OCIBundleWriter needs its
own verify that checks the post-bundle layout. However,
`write_bundle()` is called from `_process_single()` in
main.py, *after* `_fetch()`. So we need to call
`verify()` after `write_bundle()`, not inside `_fetch()`.

*Revised flow:*
```python
def _fetch(img, output):
    ...
    output.finalize()
    return stats  # verify NOT called here

def _process_single(ctx, source, destination, filters):
    ...
    stats = _fetch(input_source, output)
    if hasattr(output, 'write_bundle'):
        output.write_bundle()
    # NOW verify
    if ctx.obj.get('VERIFY'):
        writer = _get_inner_writer(output)
        results = writer.verify(
            full=ctx.obj.get('VERIFY_FULL', False))
        stats['verify_errors'] = results.error_count
        stats['verify_warnings'] = results.warning_count
    return stats
```

This means OCIBundleWriter can verify the post-bundle
layout, and DirWriter verifies the post-finalize layout.

### MountWriter.verify()

MountWriter is a direct ImageOutput subclass, not a
DirWriter. Its layout after finalize is similar to
DirWriter (manifest, catalog, layer directories). But
after `write_bundle()`, layers are overlay-mounted.

For Phase 1, MountWriter gets the default no-op verify.
It can be implemented in a later phase if needed — mount
operations are less common than dir or tar output.

### CLI flag design

```python
@click.option('--verify/--no-verify', default=True,
              help='Verify output after processing '
                   '(default: enabled)')
@click.option('--verify-full', is_flag=True,
              default=False,
              help='Full verification: re-read and '
                   'hash all layers')
```

Two separate flags rather than `--verify=full` because
Click's boolean flag syntax (`--verify/--no-verify`)
doesn't support value arguments. The `--verify-full`
flag implies `--verify`.

### Summary integration

Add `verify_errors` and `verify_warnings` to
`_print_summary()`. When verification is enabled:

```
Summary: 3 layers, 125.4 MB, 2.3s, verified OK
Summary: 3 layers, 125.4 MB, 2.3s, 2 verify errors
```

For bulk operations:
```
Summary: 47/47 images, 312 layers, 4.2 GB, 38.1s, 47/47 verified
Summary: 47/47 images, 312 layers, 4.2 GB, 38.1s, 45/47 verified, 2 verify errors
```

## Implementation steps

### Step 1: Add verify() to ImageOutput and CheckResults import

Add a concrete `verify(full=False)` method to
`ImageOutput` that returns an empty `CheckResults`.
Import `CheckResults` from `check.py`.

### Step 2: Add _expected_layers tracking to DirWriter

In `DirWriter.process_image_element()`, record each
layer's written size in `self._expected_layers` (a dict
mapping layer path to size in bytes). Also record the
config file size in `self._expected_config_size`.

### Step 3: Implement DirWriter.verify()

Override `verify()` in DirWriter to check:
- Manifest file exists and is valid JSON
- Config file exists and size matches
- Each layer file exists and size matches
- Full mode: open each layer with `tarfile.open()` to
  validate it's a valid tar

### Step 4: Add --verify/--no-verify and --verify-full flags

Add the flags to the `cli` group in main.py. Store in
`ctx.obj['VERIFY']` and `ctx.obj['VERIFY_FULL']`.

### Step 5: Wire verify into _process_single and _process_multi

Call `writer.verify()` after finalize/write_bundle in
`_process_single`. Add `verify_errors` and
`verify_warnings` to the stats dict. In
`_process_multi`, aggregate verification counts.

### Step 6: Update _print_summary with verification

Add verification counts to the summary line. Show
"verified OK" when all pass, "N verify errors" when
some fail.

### Step 7: Add unit tests

- Test DirWriter.verify() with a correctly written image
  (expect no errors).
- Test DirWriter.verify() with a missing layer file
  (expect error).
- Test DirWriter.verify() with wrong layer size (expect
  error).
- Test DirWriter.verify(full=True) with a corrupt layer
  (expect error).
- Test that --no-verify skips verification.
- Test the summary line includes verification results.

### Step 8: Update documentation

Update docs/command-reference.md, README.md,
ARCHITECTURE.md, and AGENTS.md.

## Commit plan

1. **Add verification framework and DirWriter verifier.**
   Add `verify()` to ImageOutput, implement in DirWriter,
   add `--verify/--no-verify` and `--verify-full` flags,
   wire into _process_single/_process_multi, update
   summary line. Add unit tests.

2. **Update documentation for --verify flag.**
   Update command-reference.md, README.md,
   ARCHITECTURE.md, AGENTS.md.

## Risk assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| verify() slows bulk operations | Low | Low | Default mode is stat-only, very fast |
| Filter chain complicates writer access | Low | Low | Walk _wrapped chain (already done in _fetch) |
| OCIBundleWriter post-bundle layout differs | Medium | Low | Defer OCIBundleWriter verify to later phase |
| False positives from race conditions | Very low | Medium | Verify runs single-threaded after finalize |

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
