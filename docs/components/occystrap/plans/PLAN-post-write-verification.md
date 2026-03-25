# Post-write verification for output integrity

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

Occystrap recently gained significant parallelism (httpx with
HTTP/2, concurrent multi-image processing, parallel layer
downloads and uploads). This makes bulk operations much faster
but also increases the chance that transient errors (network
glitches, rate-limiting, disk I/O issues) could silently produce
incomplete output.

The existing `check` command validates images from an *input*
source — it reads a manifest and config from a registry, then
optionally downloads and verifies all layers. But there is no
equivalent verification for *output* — after `process` writes
an image, nothing confirms that the written output is complete
and correct.

Users running bulk mirrors (e.g., `quay://` → `dir://` with
hundreds of images) need confidence that every image landed
correctly, especially when they saw transient errors scroll past
during processing.

### What exists today

**`check.py` module:**
- `CheckResults` class: accumulates errors/warnings/info with
  `error()`, `warning()`, `info()` methods and `has_errors`
  property.
- `check_metadata(manifest, config, results)`: fast-mode
  validation of manifest structure, schema version, layer count
  consistency, compression compatibility, media types.
- `check_layers(input_source, manifest, config, results)`:
  full validation that downloads layers and verifies diff_ids,
  tar format, whiteout correctness.

**`check` CLI command:**
- Takes a source URI and runs `check_metadata` (always) plus
  `check_layers` (unless `--fast`).
- Reports results in text or JSON format.
- Only works against *input* sources (registries, tarballs,
  Docker daemon).

**Output writers (`finalize()` state):**
- `DirWriter`: writes layers as files in subdirectories, writes
  `manifest-{name}-{tag}.json` and updates `catalog.json`.
- `TarWriter`: writes layers and manifest into a tarball,
  closes the tarball.
- `RegistryWriter`: pushes blobs and manifest to a registry,
  reports upload stats.
- `DockerWriter`: builds a tarball and POSTs to Docker API.
- `OCIBundleWriter` / `MountWriter`: extend DirWriter with
  OCI bundle / overlay extraction.

**Base output tracking (`ImageOutput`):**
- `_track_element(type, size)`: counts layers and bytes.
- `_total_bytes`, `_layer_count`: available after processing.

None of the output writers verify their own output after
writing. The pipeline trusts that if no exception was raised,
the output is correct.

## Mission and problem statement

Add a `--verify` flag to the `process` command that runs
post-write verification after each image completes. The
verification should confirm that the output is complete and
correct by reading back what was written and checking it
against what should have been written.

For bulk operations, provide an aggregate summary
("47/47 images verified OK" or "45/47 verified, 2 FAILED")
so users can trust the result without scrolling through logs.

The verification should be:
- **On by default** — users shouldn't have to opt in to
  correctness. A `--no-verify` flag disables it for speed.
- **Output-type-specific** — each output format has different
  things to verify.
- **Non-destructive** — verification reads but never modifies
  the output.
- **Efficient** — avoid re-downloading or re-reading more data
  than necessary. For directory output, stat files and check
  hashes. For registry output, HEAD requests for blob
  existence.

## Open questions

1. **Should `--verify` be on by default?**

   *Recommendation:* Yes, on by default with `--no-verify` to
   disable. The performance cost is small relative to the
   transfer, and the confidence benefit is high. Users who
   want maximum speed can opt out.

2. **Should verification re-read and hash every layer, or
   just check file existence and size?**

   *Recommendation:* Two levels. The default `--verify`
   checks existence and size (fast). A `--verify=full` mode
   also re-reads and hashes layers (thorough but slower).
   This mirrors the `check` command's `--fast` vs full mode.

3. **How should verification interact with filters?**

   When filters modify layer content (e.g., `exclude`,
   `normalize-timestamps`), the output layers have different
   hashes than the input layers. Verification needs to check
   against what the output *should* contain, not what the
   input had.

   *Recommendation:* The output writer knows what it wrote.
   Have each writer record what it expects (file paths, sizes,
   digests) during processing, then verify against those
   expectations. This avoids any filter confusion.

4. **Should verification failures cause a non-zero exit code?**

   *Recommendation:* Yes. Exit code 0 = all images processed
   and verified. Exit code 1 = processing or verification
   failure.

## Execution

### Phase 1: Verification framework and DirWriter verifier

Add the `--verify` / `--no-verify` flags to the `process`
command. Define an abstract `verify()` method on `ImageOutput`
that subclasses implement. Implement verification for
`DirWriter` (the most common output for bulk operations):

- Check manifest file exists and is valid JSON.
- Check config file exists and matches expected size.
- Check each layer directory and `layer.tar` file exists.
- Check each layer file size matches what was written.
- Optionally (full mode): re-read and hash each layer.

Also implement for `OCIBundleWriter` and `MountWriter` which
extend `DirWriter`.

### Phase 2: TarWriter and DockerWriter verifiers

Implement verification for `TarWriter`:

- Re-open the tarball and list its entries.
- Check manifest.json, config, and all layer tarballs present.
- Check sizes match.
- Optionally (full mode): re-read and hash layers within the
  tarball.

Implement verification for `DockerWriter`:

- Query Docker API to confirm the image was loaded.
- Check image ID matches expected config digest.

### Phase 3: RegistryWriter verifier

Implement verification for `RegistryWriter`:

- HEAD each layer blob to confirm it exists in the registry.
- HEAD the config blob.
- GET the manifest and verify it matches what was pushed.
- Optionally (full mode): GET and hash each blob.

### Phase 4: Bulk verification summary and documentation

Add aggregate reporting to `_process_multi()`:

- Track verification results per image.
- Print summary line: "47/47 images verified OK" or
  "45/47 verified, 2 FAILED: [list]".
- Update README, ARCHITECTURE.md, docs/command-reference.md.
- Add functional tests for the verification flow.

| Phase | Plan | Status |
|-------|------|--------|
| 1. Verification framework and DirWriter | PLAN-post-write-verification-phase-01-framework.md | Not started |
| 2. TarWriter and DockerWriter verifiers | PLAN-post-write-verification-phase-02-tar-docker.md | Not started |
| 3. RegistryWriter verifier | PLAN-post-write-verification-phase-03-registry.md | Not started |
| 4. Bulk summary and documentation | PLAN-post-write-verification-phase-04-summary.md | Not started |

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
* `process --verify` is on by default and exits non-zero on
  verification failure.
* Bulk operations print an aggregate verification summary.
* Each output writer has a type-specific verify() implementation.

### Future work

- Integrate with the existing `check` command so that
  `check dir:///path/to/output` works (currently `check` only
  supports input URIs).
- Add a `--verify-only` mode that re-verifies a previously
  written output without reprocessing.
- Verification for the `proxy` command's downstream writes.
- Checksums file (e.g., `SHA256SUMS`) written alongside
  directory output for external verification tools.

### Bugs fixed during this work

(None yet.)

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
