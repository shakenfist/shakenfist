# Implementing `info` and `check` subcommands for occystrap

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

I am trying something new with this document -- having a
conversation with Claude in a document instead of chat, and then
using the document as the implementation plan instead of having
Claude generate one to execute. Perhaps this is more "human in
the loop", but perhaps it is also "weird and inefficient". We'll
see I suppose.

This document is partially modelled on the western military
process of SMEAC OPORDs because I think the structure looks super
useful in general.

## Mission and problem statement

`occystrap`'s `process` command now supports some fairly
complicated content manipulation features like filtering and
changing timestamps. I'd like to implement more of those as the
need arises, but I am left thinking that its hard to catch bugs
in `occystrap`'s output. Its not a simple container converter
any more.

A recent example which raised this concern for me is this error
I am seeing in CI when using a docker local API -> filtration ->
registry push flow:

```
Unknown error message: wrong diff id
"sha256:9002b1c0c97baaa58d3bd29d02114743adaee9b3e601ededf6f65b138aae01df"
calculated on extraction
"sha256:123a078714d5ea9382d4d9f550753aefce8b34ec5ae11ae8273038d3bcbb943f",
desc "sha256:2914167652f8241cc96f909543ca0f525f067170ff80482695d1094d84abefea"
```

Now we could fix that one specific bug, but I am more interested
in ways we could ensure we don't have bugs like this **ever**. We
could for example pull the image we just pushed to the registry
in this example and then validate that the image is correct.

I am therefore proposing `occystrap`'s expansion to have two more
subcommands apart from `process`, at least partially inspired by
`qemu-img`.

### `occystrap info`

This subcommand would dump information about a given image to the
console in one of two formats -- human readable, and machine
friendly JSON depending on a global output flag. That output flag
should also be retrofitted to `process` so might well exist at
the logging layer.

The subcommand would support all of the input sources that
`process` currently supports.

### `occystrap check`

This command would perform an in-depth check of the validity of
the image: whether compression is supported; if the image will
only work on certain versions of Docker or Podman; if the manifest
elements all exist; etc etc. Literally everything we can think of.
It too would support both human and JSON output, and reuse the
`process` input sources.

## Open questions

### Do existing tools already cover info and check?

**Inspection tools:** `skopeo inspect` dumps image metadata as
JSON (digests, tags, creation date, architecture, layers).
`crane manifest` and `crane config` dump raw manifest and config
blobs. `regctl image inspect` is similar. These are adequate for
raw data but none present a concise human-readable summary
tailored to occystrap's use cases (e.g., "this image has 5
layers, 2 of which use zstd compression, total uncompressed size
is 340MB").

**Validation tools:** `crane validate` is the strongest existing
validator. It checks compressed layer digests against manifest
entries, uncompressed layer digests (diff_ids) against the config
blob, and the config blob's own digest against the manifest's
config descriptor. However, it has gaps that matter for
occystrap's layer manipulation:

* It does not check that the history array in the config is
  consistent with the layer count (non-empty history entries
  should equal the number of layers). When occystrap filters
  layers, it must also filter history entries -- no tool
  validates this.
* It does not verify that the declared mediaType matches the
  actual compression format of the blob (e.g., manifest says
  gzip but blob is actually zstd). This is a real-world
  interoperability trap.
* It does not check whiteout file preservation -- if occystrap's
  exclude filter accidentally removes `.wh.*` entries, the
  filesystem semantics are silently corrupted.
* It does not warn about Docker-vs-Podman compatibility issues
  (media type differences, `ArgsEscaped` deprecation, zstd
  support requirements).

**Conclusion:** Implementing `info` and `check` in occystrap is
justified. `crane validate` should be used in the test suite as
a baseline sanity check on occystrap's output, while
`occystrap check` adds the deeper, manipulation-aware checks
that crane misses. `diffoci` (a semantic image comparison tool)
could also be useful for regression testing.

### What information should info display?

* Image name and tag
* Manifest digest and schema version
* Media type (Docker v2 vs OCI) and what that implies for
  compatibility
* Architecture, OS, and variant
* Config digest and creation timestamp
* Number of layers, total compressed size, total uncompressed
  size
* Per-layer summary: index, compressed digest, diff_id,
  compressed size, compression format (detected from mediaType
  and/or blob magic bytes), and the corresponding history
  entry's `created_by` command (if present)
* Number of history entries and how many are `empty_layer: true`
* Labels, environment variables, entrypoint/cmd, working
  directory, exposed ports, volumes

### What things should check validate?

**Structural integrity (things that make an image invalid):**

1. `len(manifest.layers) == len(config.rootfs.diff_ids)` --
   layer count matches diff_id count
2. For each layer:
   `sha256(compressed_blob) == manifest.layers[i].digest` --
   compressed digest matches
3. For each layer:
   `sha256(uncompressed_blob) == config.rootfs.diff_ids[i]` --
   diff_id matches
4. `sha256(config_blob) == manifest.config.digest` and
   `len(config_blob) == manifest.config.size` -- config
   descriptor is correct
5. `config.rootfs.type == "layers"`
6. `manifest.schemaVersion == 2`

**History consistency (things that cause subtle runtime bugs):**

7. Number of history entries with `empty_layer != true` equals
   `len(manifest.layers)`
8. History entries are in the same order as layers

**Compression and compatibility (interoperability failures):**

9. Declared mediaType matches actual compression format of each
   layer blob (detect gzip vs zstd vs uncompressed from magic
   bytes)
10. If any layer uses zstd: warn that Docker Engine < 20.10 and
    containerd < 1.5 will not be able to pull this image
11. If manifest uses OCI media types: note that older Docker
    versions may not handle this correctly
12. If manifest uses Docker v2 media types: note that some
    OCI-only tooling may not handle this

**Filesystem integrity (corrupt container filesystem view):**

13. Whiteout files (`.wh.*` and `.wh..wh..opq`) are well-formed
14. Layer tar entries have consistent headers (no negative
    timestamps, reasonable permissions)

**Warnings (not errors, but worth reporting):**

15. Unreasonably large layers (> 1GB compressed)
16. Duplicate files across layers that could indicate missed
    deduplication opportunities
17. `config.ArgsEscaped` is set (Docker-specific, deprecated
    in OCI)

### Should process be called convert?

No. `process` is more accurate -- it does filtering, timestamp
normalization, searching, and inspection, not just format
conversion. Renaming would also break existing users. The
`qemu-img` analogy is useful for `info` and `check` but doesn't
need to extend to renaming `process`.

## Execution

### Shared prerequisite: output formatting (done)

Both `info` and `check` need human-readable and JSON output
modes. The `search` command already has `--script-friendly` but
it's implemented ad-hoc with `click.echo` calls. We should
introduce a lightweight output abstraction before implementing
either command.

**Approach:** Add a `--output-format` / `-O` option to the CLI
group in `main.py` (choices: `text`, `json`; default: `text`).
Store it in the Click context so subcommands can access it via
`ctx.obj`. This also makes it available to `process` and `search`
if we want to retrofit them later.

The formatting logic itself can be minimal -- a helper function
that takes a dict/list and either pretty-prints it as a table
(using `prettytable`, already a dependency) or dumps it as JSON.
No need for a class hierarchy.

**Files touched:** `occystrap/main.py` (add option to `cli`
group).

**Status:** Implemented. The `-O`/`--output-format` option is on
the `cli` group, stored in `ctx.obj['OUTPUT_FORMAT']`. No
existing commands use it yet -- `info` will be the first
consumer.

### Implementation plan for `info` (done)

**Step 1:** Add `info` command to `main.py`. It takes a single
`SOURCE` argument (URI string) using the same pattern as
`process`. Reuse `uri.parse_uri()` and `pipeline.py`'s
`build_input()` to construct an `ImageInput`.

**Step 2:** Add `get_manifest()` and `get_config()` methods to
the `ImageInput` base class (default: return `None`). These
fetch metadata without downloading layer blobs.

**Step 3:** Implement `get_manifest()` and `get_config()` in
each input source:

* **Registry:** `get_manifest()` fetches the distribution
  manifest via HTTP (resolving multi-arch manifest lists).
  `get_config()` fetches the config blob using the digest
  from the manifest. Both are cached on the input object.
* **Tarfile:** `get_config()` reads the config blob from
  the tarball without extracting layers. `get_manifest()`
  returns `None` (docker-save format has no distribution
  manifest).
* **Docker:** `get_config()` calls the Docker inspect API
  and transforms the result to OCI config format.
  `get_manifest()` returns `None`.
* **Dockerpush:** Both return `None` (not meaningful
  without performing a full push).

**Step 4:** Format and display the output using the shared
output formatting helper. Human-readable output uses
`prettytable` for the per-layer table and plain text for
summary fields. JSON output is a single dict with all fields.

**Files touched:** `occystrap/main.py` (new command,
`_build_info`, `_format_size`, `_print_info_text` helpers),
`occystrap/inputs/base.py` (add `get_manifest()` and
`get_config()`), `occystrap/inputs/registry.py` (implement
both), `occystrap/inputs/docker.py` (implement `get_config()`),
`occystrap/inputs/tarfile.py` (implement `get_config()`).

**Scope decision:** `info` does not download layer blobs. It
works from the manifest and config alone. This means it reports
compressed sizes from manifest descriptors but cannot report
uncompressed sizes (those would require downloading and
decompressing every layer). This is the same trade-off
`crane validate --fast` makes.

**Status:** Implemented. The `info` command works with
`registry://`, `docker://`, and `tar://` sources. Registry
sources show full detail (compressed sizes, mediaTypes,
compression format). Docker and tarball sources show
config-derived info (architecture, OS, diff_ids, history,
labels, env, etc.). 19 unit tests cover the implementation.

### Implementation plan for `check` (done)

**Step 1:** Add `check` command to `main.py`. Same `SOURCE`
argument and input selection as `info`.

**Step 2:** Implement a `CheckResult` dataclass or simple dict
structure to accumulate errors, warnings, and informational
messages. Each check produces entries tagged with a severity
(error, warning, info) and a human-readable description.

**Step 3:** Implement the structural integrity checks (items 1-6
from the check list above). These require both the manifest and
the config blob. Items 2-3 (digest verification) require
downloading and hashing every layer -- this makes `check` a slow
operation by design. Add a `--fast` flag that skips layer
download and only checks metadata consistency (items 1, 4, 5, 6,
7, 8, and the compatibility warnings).

**Step 4:** Implement the history consistency checks (items 7-8).

**Step 5:** Implement the compression and compatibility checks
(items 9-12). Item 9 requires reading the first few bytes of
each layer blob to detect the actual compression format (gzip
magic: `\x1f\x8b`, zstd magic: `\x28\xb5\x2f\xfd`). This can
piggyback on the layer download in step 3.

**Step 6:** Implement the filesystem integrity checks
(items 13-14). These require decompressing layers and scanning
tar entries. This also piggybacks on the layer download.

**Step 7:** Implement the warnings (items 15-17). These are
derived from data already collected in earlier steps.

**Step 8:** Format and display results using the shared output
formatting helper. Human-readable output should group by severity
(errors first, then warnings, then info). JSON output should be
a structured list of check results. Exit code should be non-zero
if any errors were found (useful for CI integration).

**Files touched:** `occystrap/main.py` (new command),
`occystrap/check.py` (check logic module).

**Status:** Implemented (steps 1-8). The `check` command works
with `registry://`, `docker://`, and `tar://` sources. Fast mode
checks metadata consistency (schema version, rootfs type, layer
count, history count, compression compatibility, ArgsEscaped).
Full mode additionally verifies config digest/size, layer
diff_ids, tar validity, whiteout files, and tar headers. Checks
not yet implemented: compressed blob digest verification (item 2,
already handled by input sources during download) and compression
magic vs mediaType verification (item 9, requires raw blob
access). 30 unit tests cover the implementation.

### Testing strategy (done)

**For `info`:** Create test images with known properties (specific
layer counts, compression formats, labels, history entries) and
verify `info`'s JSON output matches expected values. The JSON
output mode makes this straightforward -- parse the output and
assert on fields.

**For `check`:** We need images with known defects. Create these
programmatically in test fixtures:

* An image where `manifest.layers` has more entries than
  `config.rootfs.diff_ids` (layer count mismatch)
* An image where a layer's compressed digest doesn't match the
  manifest (corrupt digest)
* An image where the config blob's digest doesn't match the
  manifest's config descriptor (stale config reference)
* An image where history entries don't align with layers
* An image with mismatched mediaType vs actual compression

Also run `check` against known-good images produced by `process`
to verify they pass cleanly. This is the CI integration use case
from the problem statement -- after `process` produces an image,
`check` validates it.

**Existing test infrastructure:** The project uses
testtools/stestr with tox. New tests should follow the existing
patterns in `occystrap/tests/`. Functional tests that require
actual Docker/registry interaction go in
`deploy/occystrap_ci/tests/`.

**Status:** Implemented. Unit tests cover both `info` (19 tests
in `occystrap/tests/test_info.py`) and `check` (30 tests in
`occystrap/tests/test_check.py`) with synthetic images
containing known properties and defects. Functional tests in
`deploy/occystrap_ci/tests/test_info.py` (6 tests) and
`deploy/occystrap_ci/tests/test_check.py` (12 tests) validate
against real images (busybox, ubuntu) in the CI local registry.
The functional check tests include the core CI use case:
process with filters (normalize-timestamps, exclude, combined),
then check the output passes full validation. Registry
roundtrip validation is also covered.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* There are unit and functional tests for these features.
* There are a test suite of sample container images in
  `shakenfist/occystrap-testdata` that exercises these features
  and ensures they work correctly, including that their output
  agrees with other comparable tooling.
* Functional testing leverages these new commands to ensure
  that other `occystrap` commands produce valid output.
* Unit and functional tests pass.
* Documentation in `docs/` has been updated to describe these
  new features and how we use them.

### Future work

We should list obvious extensions, known issues, unrelated bugs
we encountered, and anything else we should one day do but have
chosen to defer to here so that we don't forget them.

* **Multi-architecture index validation:** `check` initially
  targets single-platform manifests. Validating image indexes
  (fat manifests) -- ensuring all platform entries point to valid
  manifests with matching architecture/OS fields -- is a natural
  extension.
* **Retrofit `--output-format` to `process` and `search`:** Once
  the output formatting infrastructure exists, the `search`
  command's ad-hoc `--script-friendly` flag could be replaced
  with the shared mechanism, and `process` could gain structured
  JSON progress reporting.
* **`check` as a post-`process` pipeline stage:** Consider
  allowing `process` to automatically run `check` on its output
  (e.g., `--verify` flag). This directly addresses the CI use
  case from the problem statement without requiring a separate
  invocation.
* **Remote-only fast checks for registries:** When the input is
  a registry, `check --fast` could use HEAD requests to verify
  blob existence without downloading anything, similar to how
  the registry output's `fetch_callback` already works.
* **`regctl image mod` as a reference comparison:** `regctl`
  provides similar manipulation capabilities (timestamps,
  compression, format conversion). Its output could be used as
  a reference in tests to verify occystrap produces equivalent
  results.
* **Compressed blob digest verification (check item 2):**
  `check` does not independently verify that
  `sha256(compressed_blob) == manifest.layers[i].digest`
  because the input sources already verify this during
  download. If we want `check` to be a standalone validator
  (e.g., for images not fetched by occystrap), we'd need raw
  blob access before the input source decompresses them.
* **Compression magic vs mediaType verification (check item
  9):** Verifying that a layer's actual compression format
  (detected from magic bytes) matches the declared mediaType
  requires reading the first few bytes of the raw compressed
  blob. The current `fetch()` interface yields decompressed
  data, so this check needs either a new raw-blob accessor or
  integration at the input source level.

### Bugs fixed during this work

* **Config diff_ids not updated after filtering:** When
  `process` applies content-modifying filters (e.g.,
  `normalize-timestamps`, `exclude`), the layer data changes
  (and thus its SHA256 diff_id), but the config blob was passed
  through with the original diff_ids. `check` correctly
  detected this as a diff-id mismatch. Fixed by adding config
  buffering and diff_id tracking to the `ImageFilter` base
  class (`occystrap/filters/base.py`). Content-modifying
  filters now buffer the config element, record new diff_ids
  as layers are processed, and forward the updated config in
  `finalize()`. This is the exact class of bug that motivated
  the `check` command in the first place (see the "wrong
  diff id" error in the problem statement).
* **Flaky `test_upload_blob_new`:** `test_process_config_file`
  submitted config upload to a `ThreadPoolExecutor` but never
  called `finalize()` or shut down the executor. Under certain
  timing, the upload thread outlived its mock scope and inflated
  call counts in subsequent tests. Fixed by adding
  `writer._executor.shutdown(wait=True)` to the test.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
